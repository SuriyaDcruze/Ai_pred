"""Tests for the Evidence & Explanation Engine (Sprint 5 · Milestone 3).

Cover the evidence-graph structure, provenance resolution (no orphans), deterministic explanation
(identical inputs → identical output + serialization), For/Against generation, missing-evidence
handling (`INSUFFICIENT_DATA`/`NOT_AVAILABLE`/`NOT_SUPPORTED`), orphan + duplicate detection,
graceful degradation, the read-only guarantee, no-advice framing, and no-engine-imports. Structure
over composed objects — no engines run. Temporary databases not required (pure over the M1 model).
"""

from __future__ import annotations

import pytest

from app.decision_intelligence.evidence import (
    DuplicateEvidenceError,
    EvidenceEngine,
    EvidenceGraph,
    EvidenceNode,
    ExplainedDecision,
    Explanation,
    ExplanationError,
    ForAgainstItem,
    MissingReason,
    OrphanedEvidenceError,
    Stance,
)
from app.decision_intelligence.models import (
    CONTRIBUTORS,
    DecisionComponent,
    DecisionIntelligence,
    DecisionStatus,
    EvidenceRef,
    Provenance,
    Subsystem,
    section_for,
)

_ADVICE = ["you should", "will win", "guarantee", "take this trade", "should buy", "buy now",
           "we recommend you"]

_PRED = {"direction": "BUY", "recommendation": "BUY", "direction_prob": 0.61, "outcome_prob": 0.62,
         "current_price": 100.0, "entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": 120.0,
         "market_regime": "BULL", "market_phase": None, "sector": "Energy", "timeframe": "1d",
         "status": "TARGET_HIT", "realised_r": 2.0, "holding_bars": 5}
_MEM = {"record_present": True, "resolved": True}
_SIM = {"neighbour_count": 5, "sample_size": 5, "win_rate": 0.8, "avg_realised_r": 1.2}
_LEARN = {"recommendation_count": 2, "pattern_count": 1, "dataset_version": "lds-1"}


def _comp(subsystem, status, *, payload=None, evidence_ids=("e",)):
    return DecisionComponent(
        subsystem=subsystem, section=section_for(subsystem), status=status,
        provenance=Provenance(subsystem=subsystem, source="p1", subsystem_version="v1"),
        payload=payload, evidence=tuple(EvidenceRef(kind="x", ref_id=i, subsystem=subsystem)
                                        for i in evidence_ids),
    )


def _di(*, status=DecisionStatus.COMPLETE, prediction=DecisionStatus.COMPLETE,
        memory=DecisionStatus.COMPLETE, similarity=DecisionStatus.COMPLETE,
        learning=DecisionStatus.COMPLETE, pred_payload=None, sim_payload=None, learn_payload=None,
        mem_payload=None):
    comps = [
        _comp(Subsystem.PREDICTION, prediction, payload=pred_payload if pred_payload is not None else _PRED),
        _comp(Subsystem.HISTORICAL_MEMORY, memory, payload=mem_payload if mem_payload is not None else _MEM),
        _comp(Subsystem.SIMILARITY, similarity, payload=sim_payload if sim_payload is not None else _SIM),
        _comp(Subsystem.LEARNING, learning, payload=learn_payload if learn_payload is not None else _LEARN),
    ]
    return DecisionIntelligence.create(prediction_id="p1", status=status, components=comps)


# --------------------------------------------------------------- graph structure
def test_evidence_graph_structure():
    exp = EvidenceEngine().explain(_di())
    root = exp.evidence_graph.root
    assert root.subsystem is Subsystem.DECISION_INTELLIGENCE
    assert tuple(c.subsystem for c in root.children) == CONTRIBUTORS      # fixed order
    labels = {c.subsystem: [g.label for g in c.children] for c in root.children}
    assert labels[Subsystem.PREDICTION] == ["Confidence", "Direction", "Risk"]
    assert labels[Subsystem.HISTORICAL_MEMORY] == ["Records", "Aggregates"]
    assert labels[Subsystem.SIMILARITY] == ["Neighbours", "Similarity Score"]
    assert labels[Subsystem.LEARNING] == ["Patterns", "Statistics", "Recommendations"]


def test_every_node_traces_to_provenance():
    exp = EvidenceEngine().explain(_di())
    nodes = exp.evidence_graph.nodes()
    assert len(nodes) == len(exp.provenance_map)                          # no orphans
    for node in nodes:
        assert node.node_id in exp.provenance_map
        assert exp.provenance_map[node.node_id]["subsystem"] == node.subsystem.value


# --------------------------------------------------------------- determinism
def test_deterministic_explanation():
    a = EvidenceEngine().explain(_di())
    b = EvidenceEngine().explain(_di())
    assert a.checksum == b.checksum and a.serialize() == b.serialize()
    assert a.evidence_graph.checksum == b.evidence_graph.checksum


def test_source_object_not_modified():
    di = _di()
    before = di.checksum
    EvidenceEngine().explain(di)
    assert di.checksum == before                                         # read-only over the input


# --------------------------------------------------------------- For / Against
def test_for_generation_complete_object():
    exp = EvidenceEngine().explain(_di())
    fors = {i.subsystem for i in exp.explanation.for_items}
    assert fors == set(CONTRIBUTORS)                                      # each complete section supports
    assert all(i.stance is Stance.FOR and i.evidence for i in exp.explanation.for_items)


def test_against_generation_for_thin_and_conflict():
    di = _di(status=DecisionStatus.PARTIAL, memory=DecisionStatus.INSUFFICIENT_DATA,
             learning=DecisionStatus.INSUFFICIENT_DATA,
             pred_payload={**_PRED, "outcome_prob": 0.39})              # outcome-model veto signal
    exp = EvidenceEngine().explain(di)
    against_subs = [i.subsystem for i in exp.explanation.against_items]
    assert Subsystem.HISTORICAL_MEMORY in against_subs and Subsystem.LEARNING in against_subs
    assert any("target-before-stop" in i.statement for i in exp.explanation.against_items)


def test_similarity_conflict_when_win_rate_low():
    di = _di(sim_payload={**_SIM, "win_rate": 0.3})
    exp = EvidenceEngine().explain(di)
    assert any(i.subsystem is Subsystem.SIMILARITY and "below 50%" in i.statement
               for i in exp.explanation.against_items)


# --------------------------------------------------------------- missing evidence
def test_missing_insufficient_and_not_supported():
    di = _di(status=DecisionStatus.PARTIAL, similarity=DecisionStatus.INSUFFICIENT_DATA)
    missing = EvidenceEngine().explain(di).explanation.missing
    by_reason = {(m.subsystem, m.facet): m.reason for m in missing}
    assert by_reason[(Subsystem.SIMILARITY, None)] is MissingReason.INSUFFICIENT_DATA
    # memory 'aggregates' facet is never composed by M2 → NOT_SUPPORTED (a complete section)
    assert by_reason[(Subsystem.HISTORICAL_MEMORY, "aggregates")] is MissingReason.NOT_SUPPORTED


def test_missing_not_available_on_error():
    di = _di(status=DecisionStatus.ERROR, learning=DecisionStatus.ERROR)
    missing = EvidenceEngine().explain(di).explanation.missing
    assert any(m.subsystem is Subsystem.LEARNING and m.reason is MissingReason.NOT_AVAILABLE
               for m in missing)


# --------------------------------------------------------------- graceful degradation
def test_prediction_only_degradation():
    di = _di(status=DecisionStatus.PARTIAL, memory=DecisionStatus.INSUFFICIENT_DATA,
             similarity=DecisionStatus.INSUFFICIENT_DATA, learning=DecisionStatus.INSUFFICIENT_DATA)
    exp = EvidenceEngine().explain(di)
    assert {i.subsystem for i in exp.explanation.for_items} == {Subsystem.PREDICTION}
    assert len([i for i in exp.explanation.against_items
                if i.subsystem in (Subsystem.HISTORICAL_MEMORY, Subsystem.SIMILARITY, Subsystem.LEARNING)]) == 3


# --------------------------------------------------------------- descriptive / no advice
def test_no_advice_language():
    exp = EvidenceEngine().explain(_di())
    blob = " ".join([exp.explanation.summary, exp.explanation.disclaimer,
                     *[i.statement for i in exp.explanation.for_items],
                     *[i.statement for i in exp.explanation.against_items]]).lower()
    for phrase in _ADVICE:
        assert phrase not in blob
    assert "not a prediction" in exp.explanation.disclaimer.lower()


# --------------------------------------------------------------- validation
def test_rejects_non_decision_object():
    with pytest.raises(ExplanationError):
        EvidenceEngine().explain({"not": "a decision"})


def test_validate_detects_duplicate_nodes():
    prov = Provenance(subsystem=Subsystem.PREDICTION)
    dup1 = EvidenceNode(node_id="dup", path="a", label="A", subsystem=Subsystem.PREDICTION,
                        status=DecisionStatus.COMPLETE, provenance=prov)
    dup2 = EvidenceNode(node_id="dup", path="b", label="B", subsystem=Subsystem.PREDICTION,
                        status=DecisionStatus.COMPLETE, provenance=prov)
    root = EvidenceNode(node_id="root", path="d", label="D", subsystem=Subsystem.DECISION_INTELLIGENCE,
                        status=DecisionStatus.COMPLETE, provenance=prov, children=(dup1, dup2))
    graph = EvidenceGraph(root=root, decision_id="d1", checksum="c")
    explained = ExplainedDecision(
        decision_id="d1", prediction_id="p1", decision_intelligence_version="di-1", source_checksum="s",
        evidence_graph=graph, explanation=Explanation("s", "d", (), (), ()), provenance_map={}, checksum="c",
    )
    with pytest.raises(DuplicateEvidenceError):
        EvidenceEngine._validate(explained, graph)


def test_validate_detects_orphan_reference():
    prov = Provenance(subsystem=Subsystem.PREDICTION)
    root = EvidenceNode(node_id="root", path="d", label="D", subsystem=Subsystem.DECISION_INTELLIGENCE,
                        status=DecisionStatus.COMPLETE, provenance=prov)
    graph = EvidenceGraph(root=root, decision_id="d1", checksum="c")
    orphan = ForAgainstItem(stance=Stance.FOR, subsystem=Subsystem.PREDICTION, node_id="ghost",
                            statement="x")
    explained = ExplainedDecision(
        decision_id="d1", prediction_id="p1", decision_intelligence_version="di-1", source_checksum="s",
        evidence_graph=graph, explanation=Explanation("s", "d", (orphan,), (), ()), provenance_map={},
        checksum="c",
    )
    with pytest.raises(OrphanedEvidenceError):
        EvidenceEngine._validate(explained, graph)


# --------------------------------------------------------------- serialization / isolation
def test_deterministic_serialization_bytes():
    a = EvidenceEngine().explain(_di()).serialize()
    b = EvidenceEngine().explain(_di()).serialize()
    assert a == b and a.startswith("{") and "provenance_map" in a


def test_evidence_module_imports_no_engine():
    import ast

    import app.decision_intelligence.evidence as ev
    with open(ev.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
