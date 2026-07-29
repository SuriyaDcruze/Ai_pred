"""Tests for the Decision Intelligence Composition Engine (Sprint 5 · Milestone 2).

Cover deterministic composition (identical inputs → identical object + checksum), graceful
degradation (missing memory / similarity / learning → `INSUFFICIENT_DATA`, object still returned),
the required-prediction anchor, per-source errors → `ERROR`, integrity (duplicate section rejected),
version tracking, read-only (no writes), immutability, a real Learning-provider integration, and
that the composition modules import no engine. Temporary databases only.
"""

from __future__ import annotations

import pytest

from app.decision_intelligence.compose import (
    CompositionEngine,
    LearningSource,
    MemorySource,
    MissingPredictionError,
    PredictionSource,
    SimilaritySource,
    SourceAdapter,
    build_engine,
)
from app.decision_intelligence.models import (
    DecisionComponent,
    DecisionStatus,
    EvidenceRef,
    Provenance,
    SchemaConsistencyError,
    Subsystem,
    section_for,
)
from app.decision_intelligence.providers import LearningPipelineProvider
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore

_TS = 1_700_000_000


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


@pytest.fixture()
def retrieval(stores):
    ps, ms = stores
    return RetrievalEngine(ps, ms)


def _seed(ps, *, i, sector="Energy", regime="BULL", tf="1d", resolve_r=None):
    rec = PredictionRecord(
        symbol=f"S{i}.NS", exchange="NSE", timeframe=tf, current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0, target1=110.0,
        direction_prob=0.61, outcome_prob=0.62, decision_score=0.3, sector=sector,
        market_regime=regime, prediction_model_version="pred-1", outcome_model_version="out-1",
        feature_version="feat-1", status=PredictionStatus.ACTIVE,
    )
    rec.created_at = f"2026-01-01T00:00:{i:02d}+00:00"
    ps.create(rec)
    if resolve_r is not None:
        ps.update_resolution(
            rec.prediction_id,
            status=PredictionStatus.TARGET_HIT if resolve_r > 0 else PredictionStatus.STOP_HIT,
            resolved_price=110.0 if resolve_r > 0 else 95.0, resolution_reason="t",
            realised_r=resolve_r, holding_bars=5,
        )
    return rec.prediction_id


# ---- a controllable fake source (for precise status combinations) --------------------------
class FakeSource(SourceAdapter):
    def __init__(self, subsystem, status=DecisionStatus.COMPLETE, *, raise_exc=None,
                 evidence_ids=(), versions=None):
        self.subsystem = subsystem
        self._status = status
        self._raise = raise_exc
        self._ev = tuple(evidence_ids)
        self._versions = versions or {}

    def compose(self, prediction_id, record):
        if self._raise is not None:
            raise self._raise
        return DecisionComponent(
            subsystem=self.subsystem, section=section_for(self.subsystem), status=self._status,
            provenance=Provenance(subsystem=self.subsystem, source=prediction_id),
            payload={"fake": True},
            evidence=tuple(EvidenceRef(kind="x", ref_id=i, subsystem=self.subsystem) for i in self._ev),
        )

    def versions(self, record, component):
        return self._versions


def _engine_with_fakes(ps, *, memory=None, similarity=None, learning=None):
    return CompositionEngine(
        prediction=PredictionSource(ps),
        memory=memory or FakeSource(Subsystem.HISTORICAL_MEMORY, DecisionStatus.COMPLETE),
        similarity=similarity or FakeSource(Subsystem.SIMILARITY, DecisionStatus.COMPLETE),
        learning=learning or FakeSource(Subsystem.LEARNING, DecisionStatus.COMPLETE),
    )


# --------------------------------------------------------------- degradation / status
def test_prediction_only_degrades_to_partial(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1)                                   # unresolved, no similarity engine/provider
    di = build_engine(prediction_store=ps, retrieval=retrieval).compose(pid)
    assert di.prediction_id == pid
    assert di.component(Subsystem.PREDICTION).status is DecisionStatus.COMPLETE
    assert di.component(Subsystem.HISTORICAL_MEMORY).status is DecisionStatus.INSUFFICIENT_DATA
    assert di.component(Subsystem.SIMILARITY).status is DecisionStatus.INSUFFICIENT_DATA
    assert di.component(Subsystem.LEARNING).status is DecisionStatus.INSUFFICIENT_DATA
    assert di.status is DecisionStatus.PARTIAL             # anchor present, context thin


def test_prediction_section_is_verbatim(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1)
    payload = build_engine(prediction_store=ps, retrieval=retrieval).compose(pid).component(
        Subsystem.PREDICTION).payload
    assert payload["direction"] == "BUY" and payload["outcome_prob"] == 0.62
    assert payload["entry"] == 100.0 and payload["stop"] == 95.0


def test_resolved_prediction_memory_complete(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1, resolve_r=2.0)
    di = build_engine(prediction_store=ps, retrieval=retrieval).compose(pid)
    assert di.component(Subsystem.HISTORICAL_MEMORY).status is DecisionStatus.COMPLETE


def test_all_sections_complete_is_complete(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1, resolve_r=2.0)
    di = _engine_with_fakes(ps).compose(pid)
    assert di.status is DecisionStatus.COMPLETE
    assert all(c.status is DecisionStatus.COMPLETE for c in di.components)


def test_missing_learning_alone_is_partial(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1, resolve_r=2.0)
    eng = _engine_with_fakes(ps, learning=FakeSource(Subsystem.LEARNING, DecisionStatus.INSUFFICIENT_DATA))
    di = eng.compose(pid)
    assert di.status is DecisionStatus.PARTIAL
    assert di.component(Subsystem.LEARNING).status is DecisionStatus.INSUFFICIENT_DATA


# --------------------------------------------------------------- determinism
def test_deterministic_composition(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1, resolve_r=2.0)
    eng = build_engine(prediction_store=ps, retrieval=retrieval)
    a, b = eng.compose(pid), eng.compose(pid)
    assert a.checksum == b.checksum and a.decision_id == b.decision_id
    assert a.stable_dict() == b.stable_dict()


# --------------------------------------------------------------- required anchor / errors
def test_missing_prediction_raises(retrieval, stores):
    ps, _ = stores
    eng = build_engine(prediction_store=ps, retrieval=retrieval)
    with pytest.raises(MissingPredictionError):
        eng.compose("does-not-exist")
    with pytest.raises(MissingPredictionError):
        eng.compose("")


def test_source_error_yields_error_section_not_raise(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1, resolve_r=2.0)
    eng = _engine_with_fakes(ps, similarity=FakeSource(Subsystem.SIMILARITY, raise_exc=RuntimeError("boom")))
    di = eng.compose(pid)                                  # object still returned
    assert di.component(Subsystem.SIMILARITY).status is DecisionStatus.ERROR
    assert di.status is DecisionStatus.ERROR


def test_duplicate_section_rejected(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1, resolve_r=2.0)
    # A fake that claims the PREDICTION section in the memory slot → two prediction sections.
    eng = _engine_with_fakes(ps, memory=FakeSource(Subsystem.PREDICTION, DecisionStatus.COMPLETE))
    with pytest.raises(SchemaConsistencyError):
        eng.compose(pid)


# --------------------------------------------------------------- version tracking
def test_version_tracking(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1, resolve_r=2.0)
    eng = _engine_with_fakes(
        ps,
        similarity=FakeSource(Subsystem.SIMILARITY, versions={"embedding_version": "sim-emb-1"}),
        learning=FakeSource(Subsystem.LEARNING, versions={"learning_version": "lrn-1",
                                                          "dataset_version": "lds-1"}),
    )
    uv = eng.compose(pid).upstream_versions
    assert uv.prediction_model_version == "pred-1" and uv.outcome_model_version == "out-1"
    assert uv.feature_version == "feat-1" and uv.embedding_version == "sim-emb-1"
    assert uv.learning_version == "lrn-1" and uv.dataset_version == "lds-1"


# --------------------------------------------------------------- read-only / immutability
def test_composition_performs_no_writes(retrieval, stores):
    ps, ms = stores
    pid = _seed(ps, i=1, resolve_r=2.0)
    before = ps.count()
    build_engine(prediction_store=ps, retrieval=retrieval).compose(pid)
    assert ps.count() == before                           # predictions unchanged
    for table in ("learning_runs", "learning_patterns", "learning_recommendations"):
        assert ms._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0


def test_object_is_immutable(retrieval, stores):
    import dataclasses
    ps, _ = stores
    pid = _seed(ps, i=1)
    di = build_engine(prediction_store=ps, retrieval=retrieval).compose(pid)
    with pytest.raises(dataclasses.FrozenInstanceError):
        di.status = DecisionStatus.COMPLETE               # type: ignore[misc]


# --------------------------------------------------------------- real learning provider
def test_real_learning_provider_composes_complete(retrieval, stores):
    ps, _ = stores
    ids = [_seed(ps, i=i, sector="Energy", resolve_r=2.0) for i in range(12)]   # 12 sector wins
    provider = LearningPipelineProvider(retrieval, min_corpus=1, min_sample=10, min_evidence=3)
    eng = build_engine(prediction_store=ps, retrieval=retrieval, learning_provider=provider)
    di = eng.compose(ids[0])
    learning = di.component(Subsystem.LEARNING)
    assert learning.status is DecisionStatus.COMPLETE      # a validated sector observation matched
    assert learning.payload["recommendation_count"] >= 1 and learning.evidence
    assert di.upstream_versions.learning_version == "lrn-1"


def test_real_learning_provider_thin_corpus_is_insufficient(retrieval, stores):
    ps, _ = stores
    pid = _seed(ps, i=1, sector="Energy", resolve_r=2.0)   # only 1 trade
    provider = LearningPipelineProvider(retrieval, min_corpus=30, min_sample=30)
    eng = build_engine(prediction_store=ps, retrieval=retrieval, learning_provider=provider)
    assert eng.compose(pid).component(Subsystem.LEARNING).status is DecisionStatus.INSUFFICIENT_DATA


# --------------------------------------------------------------- isolation
def test_composition_modules_import_no_engine():
    import ast

    import app.decision_intelligence.compose as comp
    import app.decision_intelligence.providers as prov
    for module in (comp, prov):
        with open(module.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported), module.__name__
