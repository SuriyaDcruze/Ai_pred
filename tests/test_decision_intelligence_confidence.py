"""Tests for the Composite Confidence & Prioritisation Engine (Sprint 5 · Milestone 4).

Cover deterministic confidence generation, the **core distinction** (composite confidence is an
evidence-quality indicator, NOT a prediction/trading confidence — a high-confidence prediction with
no support still scores low), evidence penalties, conflict detection + handling, missing evidence,
prioritisation + deterministic ordering, serialization, validation (bad score/factor/reference), the
read-only guarantee, no-signal language, and no-engine-imports. Structure over composed objects — no
engines run.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.decision_intelligence.confidence import (
    CompositeConfidence,
    ConfidenceEngine,
    ConfidenceError,
    ConfidenceLevel,
    ConflictKind,
    EvidenceQuality,
    InvalidConfidenceError,
    prioritise,
)
from app.decision_intelligence.evidence import EvidenceEngine
from app.decision_intelligence.models import (
    DecisionComponent,
    DecisionIntelligence,
    DecisionStatus,
    EvidenceRef,
    Provenance,
    Subsystem,
    UpstreamVersions,
    section_for,
)

_ADVICE = ["you should", "buy now", "sell now", "take this trade", " buy ", " sell ", "hold ",
           "entry", "exit", "recommendation to"]

_PRED = {"direction": "BUY", "recommendation": "BUY", "direction_prob": 0.61, "outcome_prob": 0.62,
         "entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": 120.0, "timeframe": "1d"}
_MEM = {"record_present": True, "resolved": True}
_SIM = {"neighbour_count": 6, "sample_size": 6, "win_rate": 0.8, "avg_realised_r": 1.2}
_LEARN = {"recommendation_count": 2, "pattern_count": 3, "dataset_version": "lds-1"}


def _comp(subsystem, status, *, payload=None, source="p1", version="v1"):
    return DecisionComponent(
        subsystem=subsystem, section=section_for(subsystem), status=status,
        provenance=Provenance(subsystem=subsystem, source=source, subsystem_version=version),
        payload=payload, evidence=(EvidenceRef(kind="x", ref_id="e", subsystem=subsystem),),
    )


def _di(*, status=DecisionStatus.COMPLETE, prediction=DecisionStatus.COMPLETE,
        memory=DecisionStatus.COMPLETE, similarity=DecisionStatus.COMPLETE,
        learning=DecisionStatus.COMPLETE, pred_payload=None, sim_payload=None,
        pred_source="p1", pred_version="pred-1", upstream=None, prediction_id="p1"):
    comps = [
        _comp(Subsystem.PREDICTION, prediction, payload=pred_payload if pred_payload is not None else _PRED,
              source=pred_source, version=pred_version),
        _comp(Subsystem.HISTORICAL_MEMORY, memory, payload=_MEM),
        _comp(Subsystem.SIMILARITY, similarity, payload=sim_payload if sim_payload is not None else _SIM),
        _comp(Subsystem.LEARNING, learning, payload=_LEARN),
    ]
    return DecisionIntelligence.create(prediction_id=prediction_id, status=status, components=comps,
                                       upstream_versions=upstream or UpstreamVersions())


# --------------------------------------------------------------- core distinction
def test_composite_is_evidence_quality_not_prediction_confidence():
    # A very confident PREDICTION with NO other evidence must still score LOW composite confidence.
    di = _di(status=DecisionStatus.PARTIAL, memory=DecisionStatus.INSUFFICIENT_DATA,
             similarity=DecisionStatus.INSUFFICIENT_DATA, learning=DecisionStatus.INSUFFICIENT_DATA,
             pred_payload={**_PRED, "direction_prob": 0.95})
    cc = ConfidenceEngine().assess(di)
    assert cc.score < 0.5 and cc.level in (ConfidenceLevel.LOW, ConfidenceLevel.INSUFFICIENT)
    assert "not a probability of success" in cc.explanation.lower()


def test_complete_object_scores_high():
    cc = ConfidenceEngine().assess(_di())
    assert cc.level is ConfidenceLevel.HIGH and cc.score >= 0.75
    assert len(cc.strengths) == 4 and not cc.conflicts


# --------------------------------------------------------------- determinism
def test_deterministic_confidence():
    a = ConfidenceEngine().assess(_di())
    b = ConfidenceEngine().assess(_di())
    assert a.checksum == b.checksum and a.score == b.score and a.serialize() == b.serialize()


def test_source_object_not_modified():
    di = _di()
    before = di.checksum
    ConfidenceEngine().assess(di)
    assert di.checksum == before


# --------------------------------------------------------------- penalties / factors
def test_missing_evidence_penalised():
    cc = ConfidenceEngine().assess(_di(status=DecisionStatus.PARTIAL,
                                       learning=DecisionStatus.INSUFFICIENT_DATA))
    reasons = " ".join(p.reason for p in cc.penalties)
    assert "learning evidence weak or absent" in reasons.lower()
    assert cc.evidence_quality.complete_sections == 3


def test_factors_trace_to_nodes():
    di = _di()
    explained = EvidenceEngine().explain(di)
    cc = ConfidenceEngine().assess(di, explained)
    node_ids = {n.node_id for n in explained.evidence_graph.nodes()}
    assert all(f.node_ref in node_ids for f in cc.factors)      # every factor is traceable


# --------------------------------------------------------------- conflicts
def test_outcome_disagreement_conflict():
    di = _di(pred_payload={**_PRED, "outcome_prob": 0.39})       # outcome model vetoes the BUY
    cc = ConfidenceEngine().assess(di)
    kinds = {c.kind for c in cc.conflicts}
    assert ConflictKind.OUTCOME_DISAGREEMENT in kinds
    assert cc.score < ConfidenceEngine().assess(_di()).score     # conflict lowers the score


def test_similarity_disagreement_conflict():
    cc = ConfidenceEngine().assess(_di(sim_payload={**_SIM, "win_rate": 0.3}))
    assert any(c.kind is ConflictKind.SIMILARITY_DISAGREEMENT for c in cc.conflicts)


def test_incomplete_provenance_conflict():
    cc = ConfidenceEngine().assess(_di(pred_source=None))        # COMPLETE section, no source id
    assert any(c.kind is ConflictKind.INCOMPLETE_PROVENANCE for c in cc.conflicts)


def test_version_mismatch_conflict():
    di = _di(pred_version="pred-2", upstream=UpstreamVersions(prediction_model_version="pred-1"))
    cc = ConfidenceEngine().assess(di)
    assert any(c.kind is ConflictKind.VERSION_MISMATCH for c in cc.conflicts)


def test_conflicts_recorded_in_warnings():
    cc = ConfidenceEngine().assess(_di(pred_payload={**_PRED, "outcome_prob": 0.2}))
    assert any("OUTCOME_DISAGREEMENT" in w for w in cc.warnings)


# --------------------------------------------------------------- prioritisation
def test_prioritisation_orders_by_evidence_strength():
    strong = ConfidenceEngine().assess(_di(prediction_id="strong"))
    weak = ConfidenceEngine().assess(_di(prediction_id="weak", status=DecisionStatus.PARTIAL,
                                         memory=DecisionStatus.INSUFFICIENT_DATA,
                                         similarity=DecisionStatus.INSUFFICIENT_DATA,
                                         learning=DecisionStatus.INSUFFICIENT_DATA))
    assert strong.prioritisation_score > weak.prioritisation_score
    assert [c.decision_id for c in prioritise([weak, strong])] == [strong.decision_id, weak.decision_id]


def test_prioritisation_deterministic_tie_break():
    a = ConfidenceEngine().assess(_di(prediction_id="aaa"))
    b = ConfidenceEngine().assess(_di(prediction_id="bbb"))
    # identical evidence ⇒ identical prioritisation ⇒ tie-broken by decision_id
    order = [c.decision_id for c in prioritise([b, a])]
    assert order == sorted(order)


# --------------------------------------------------------------- validation
def test_rejects_non_decision():
    with pytest.raises(ConfidenceError):
        ConfidenceEngine().assess({"not": "a decision"})


def test_mismatched_evidence_rejected():
    di = _di(prediction_id="p1")
    other = EvidenceEngine().explain(_di(prediction_id="other"))
    with pytest.raises(ConfidenceError):
        ConfidenceEngine().assess(di, other)


def test_validate_rejects_out_of_range_score():
    cc = ConfidenceEngine().assess(_di())
    bad = dataclasses.replace(cc, score=1.5)
    with pytest.raises(InvalidConfidenceError):
        ConfidenceEngine._validate(bad, EvidenceEngine().explain(_di()))


# --------------------------------------------------------------- language / isolation
def test_no_trading_signal_language():
    cc = ConfidenceEngine().assess(_di())
    blob = (cc.explanation + " " + " ".join(cc.warnings) + " "
            + " ".join(s.reason for s in cc.strengths)).lower()
    assert "not a probability of success" in cc.explanation.lower()
    for phrase in ("you should", "buy now", "sell now", "take this trade"):
        assert phrase not in blob


def test_evidence_quality_summary_present():
    cc = ConfidenceEngine().assess(_di())
    eq = cc.evidence_quality
    assert isinstance(eq, EvidenceQuality) and eq.complete_sections == 4
    assert eq.completeness == 1.0 and eq.provenance_completeness == 1.0


def test_confidence_module_imports_no_engine():
    import ast

    import app.decision_intelligence.confidence as conf
    with open(conf.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
