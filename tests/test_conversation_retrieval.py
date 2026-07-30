"""Tests for the Retrieval Orchestrator (Sprint 6 · Milestone 3).

Cover retrieval routing, source-adapter behaviour, context merging, availability handling
(AVAILABLE / INSUFFICIENT_DATA / NOT_AVAILABLE / NOT_SUPPORTED / ERROR), validation, deterministic
output + serialization, versioning, error handling, a real in-process Decision-Intelligence
integration, and no-engine imports (core). Retrieval only — no generation, no prompts, no LLM.
"""

from __future__ import annotations

import pytest

from app.conversation.intent import (
    Intent,
    IntentClassification,
    IntentClassifier,
    IntentValidation,
)
from app.conversation.retrieval import (
    RETRIEVAL_VERSION,
    DecisionIntelligenceSource,
    InvalidRetrievalRequestError,
    RetrievalAvailability,
    RetrievalOrchestrator,
    RetrievalResult,
    RetrievalTarget,
)
from app.conversation.sources import InProcessSource
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore


# ---- canned Decision Intelligence payload + fake source ------------------------------------
def _payload(*, status="PARTIAL", mem="INSUFFICIENT_DATA", sim="INSUFFICIENT_DATA",
            learn="INSUFFICIENT_DATA", pid="p1"):
    def comp(subsystem, st, payload):
        return {"subsystem": subsystem, "section": subsystem, "status": st,
                "provenance": {"subsystem": subsystem}, "payload": payload,
                "evidence": [{"kind": subsystem, "ref_id": pid, "subsystem": subsystem}]}
    decision = {"decision_id": "d1", "prediction_id": pid, "decision_intelligence_version": "di-1",
                "status": status, "upstream_versions": {}, "checksum": "dchk", "components": [
                    comp("prediction", "COMPLETE", {"direction": "BUY", "direction_prob": 0.61}),
                    comp("historical_memory", mem, {"record_present": True}),
                    comp("similarity", sim, {"neighbour_count": 0}),
                    comp("learning", learn, {"recommendation_count": 0})]}
    return {"versions": {"decision_intelligence_version": "di-1"}, "decision": decision,
            "evidence": {"graph": {}, "provenance_map": {}, "checksum": "echk"},
            "explanation": {"summary": "s", "disclaimer": "d", "for": [], "against": [], "missing": []},
            "confidence": {"score": 0.3, "level": "LOW", "checksum": "cchk"},
            "prioritisation": {"score": 0.25, "level": "LOW"}}


class FakeSource(DecisionIntelligenceSource):
    def __init__(self, payload=None, *, raise_exc=None, by_symbol="__same__"):
        self._payload = payload
        self._raise = raise_exc
        self._by_symbol = payload if by_symbol == "__same__" else by_symbol

    def fetch_decision(self, prediction_id):
        if self._raise:
            raise self._raise
        return self._payload

    def fetch_decision_by_symbol(self, symbol):
        if self._raise:
            raise self._raise
        return self._by_symbol

    def fetch_health(self):
        return {"status": "ready", "ready": True, "decision_intelligence_version": "di-1"}

    def fetch_version(self):
        return {"api_version": "1", "decision_intelligence_version": "di-1", "schema_version": "di-1"}


def _clf(intent, entities=None):
    return IntentClassification(intent=intent, confidence=1.0, matched_rules=(),
                                entities=entities or {}, validation=IntentValidation(True))


def _orch(payload=None, **kw):
    return RetrievalOrchestrator(FakeSource(payload, **kw))


# --------------------------------------------------------------- routing
def test_routing_explain_prediction():
    r = _orch(_payload()).retrieve(_clf(Intent.EXPLAIN_PREDICTION, {"prediction_id": "p1"}))
    assert [c.target for c in r.components] == [RetrievalTarget.DECISION_SUMMARY, RetrievalTarget.EXPLANATION]
    assert r.components[0].availability is RetrievalAvailability.AVAILABLE


def test_routing_evidence_confidence():
    ev = _orch(_payload()).retrieve(_clf(Intent.SHOW_EVIDENCE, {"prediction_id": "p1"}))
    assert ev.components[0].target is RetrievalTarget.EVIDENCE and ev.components[0].content["checksum"] == "echk"
    cc = _orch(_payload()).retrieve(_clf(Intent.WHY_CONFIDENCE, {"prediction_id": "p1"}))
    assert cc.components[0].content["level"] == "LOW"


def test_standalone_health_version_need_no_subject():
    h = _orch().retrieve(_clf(Intent.HEALTH))
    assert h.components[0].target is RetrievalTarget.HEALTH and h.availability is RetrievalAvailability.AVAILABLE
    v = _orch().retrieve(_clf(Intent.VERSION))
    assert v.components[0].content["decision_intelligence_version"] == "di-1"


def test_help_and_unknown_are_not_supported():
    for intent in (Intent.HELP, Intent.UNKNOWN):
        r = _orch().retrieve(_clf(intent))
        assert r.components == () and r.availability is RetrievalAvailability.NOT_SUPPORTED


# --------------------------------------------------------------- availability
def test_section_availability_maps_from_status():
    hist = _orch(_payload(mem="COMPLETE")).retrieve(_clf(Intent.HISTORICAL_COMPARISON, {"prediction_id": "p1"}))
    assert hist.components[0].availability is RetrievalAvailability.AVAILABLE
    thin = _orch(_payload(learn="INSUFFICIENT_DATA")).retrieve(_clf(Intent.LEARNING_SUMMARY, {"prediction_id": "p1"}))
    assert thin.components[0].availability is RetrievalAvailability.INSUFFICIENT_DATA


def test_missing_subject_is_not_available():
    r = _orch(_payload()).retrieve(_clf(Intent.SHOW_EVIDENCE))          # no entities
    assert r.components[0].availability is RetrievalAvailability.NOT_AVAILABLE
    assert "no prediction or symbol" in r.components[0].note


def test_prediction_not_found_is_not_available():
    r = _orch(None).retrieve(_clf(Intent.SHOW_EVIDENCE, {"prediction_id": "ghost"}))
    assert r.components[0].availability is RetrievalAvailability.NOT_AVAILABLE
    assert "not found" in r.components[0].note


def test_source_error_is_error():
    r = _orch(raise_exc=RuntimeError("boom")).retrieve(_clf(Intent.SHOW_EVIDENCE, {"prediction_id": "p1"}))
    assert r.components[0].availability is RetrievalAvailability.ERROR and r.availability is RetrievalAvailability.ERROR


# --------------------------------------------------------------- merge / citations
def test_context_merge_and_citations():
    r = _orch(_payload()).retrieve(_clf(Intent.EXPLAIN_PREDICTION, {"prediction_id": "p1"}))
    assert r.context.subject_kind == "prediction" and r.context.subject_id == "p1"
    assert set(r.context.data) == {"DECISION_SUMMARY", "EXPLANATION"}
    assert any(c.kind == "decision" and c.ref_id == "d1" for c in r.citations)
    # citations are de-duplicated across components
    assert len(r.citations) == len({(c.kind, c.ref_id, c.source) for c in r.citations})


def test_symbol_subject_resolves():
    r = _orch(_payload(pid="p9")).retrieve(_clf(Intent.SHOW_EVIDENCE, {"symbol": "BTCUSDT"}))
    assert r.context.subject_kind == "symbol" and r.components[0].availability is RetrievalAvailability.AVAILABLE


# --------------------------------------------------------------- determinism / serialization / versioning
def test_deterministic_retrieval():
    a = _orch(_payload()).retrieve(_clf(Intent.SHOW_EVIDENCE, {"prediction_id": "p1"}))
    b = _orch(_payload()).retrieve(_clf(Intent.SHOW_EVIDENCE, {"prediction_id": "p1"}))
    assert a.checksum == b.checksum and a.serialize() == b.serialize()


def test_serialization_excludes_volatile_timestamp():
    r = _orch(_payload()).retrieve(_clf(Intent.SHOW_EVIDENCE, {"prediction_id": "p1"}))
    assert "retrieved_at" not in r.stable_dict() and "retrieved_at" in r.to_dict()
    assert r.request.version == RETRIEVAL_VERSION and r.decision_intelligence_version == "di-1"


def test_invalid_request_rejected():
    with pytest.raises(InvalidRetrievalRequestError):
        _orch().retrieve({"not": "a classification"})


# --------------------------------------------------------------- integration (real Decision Intelligence)
@pytest.fixture()
def stores(tmp_path):
    path = str(tmp_path / "prediction_history.db")
    ps = PredictionStore(path=path)
    ms = MemoryStore(path=path)
    try:
        yield ps, ms
    finally:
        ps.close()
        ms.close()


def _seed(ps, *, i, symbol="RELIANCE.NS", resolve_r=2.0):
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe="1d", current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=1_700_000_000 + i, entry=100.0, stop=95.0,
        target1=110.0, direction_prob=0.61, outcome_prob=0.62, sector="Energy", market_regime="BULL",
        prediction_model_version="pred-1", feature_version="feat-1", status=PredictionStatus.ACTIVE)
    rec.created_at = f"2026-01-01T00:00:{i:02d}+00:00"
    ps.create(rec)
    if resolve_r is not None:
        ps.update_resolution(rec.prediction_id, status=PredictionStatus.TARGET_HIT,
                             resolved_price=110.0, resolution_reason="t", realised_r=resolve_r,
                             holding_bars=5)
    return rec.prediction_id


def test_inprocess_source_integration(stores):
    ps, ms = stores
    pid = _seed(ps, i=1)
    orch = RetrievalOrchestrator(InProcessSource(ps, RetrievalEngine(ps, ms)))
    r = orch.retrieve(_clf(Intent.EXPLAIN_PREDICTION, {"prediction_id": pid}))
    summary = next(c for c in r.components if c.target is RetrievalTarget.DECISION_SUMMARY)
    assert summary.availability is RetrievalAvailability.AVAILABLE
    assert summary.content["prediction"]["direction"] == "BUY"
    assert r.decision_intelligence_version == "di-1"


def test_inprocess_source_symbol_and_notfound(stores):
    ps, ms = stores
    _seed(ps, i=1, symbol="RELIANCE.NS")
    orch = RetrievalOrchestrator(InProcessSource(ps, RetrievalEngine(ps, ms)))
    ok = orch.retrieve(_clf(Intent.SHOW_EVIDENCE, {"symbol": "RELIANCE.NS"}))
    assert ok.components[0].availability is RetrievalAvailability.AVAILABLE
    missing = orch.retrieve(_clf(Intent.SHOW_EVIDENCE, {"symbol": "NOSUCH.NS"}))
    assert missing.components[0].availability is RetrievalAvailability.NOT_AVAILABLE


# --------------------------------------------------------------- isolation
def test_retrieval_core_imports_no_engine():
    import ast

    import app.conversation.retrieval as ret
    with open(ret.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.decision_intelligence",
                 "app.memory", "app.similarity", "app.learning", "app.forward_testing", "openai")
    for name in imported:
        assert not name.startswith(forbidden), f"orchestrator core must not import {name}"


def test_sources_imports_no_prediction_outcome_engine():
    import ast

    import app.conversation.sources as src
    with open(src.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
