"""Tests for the Decision Intelligence domain model & composition contract (Sprint 5 · M1).

Cover object creation, deterministic + immutable identifiers, the canonical states, the composition
contract (no subsystem populates another's section), provenance metadata (required even when data is
unavailable), version metadata, checksum presence + determinism, serialization round-trips
(dict + row), immutability, and that the module imports no engine. Structure only — no composition,
no engine reads, no API. Temporary databases only (none needed here — pure models).
"""

from __future__ import annotations

import dataclasses

import pytest

from app.decision_intelligence.models import (
    COMPOSITION_CONTRACT,
    CONTRIBUTORS,
    DECISION_INTELLIGENCE_VERSION,
    DecisionComponent,
    DecisionIntelligence,
    DecisionStatus,
    EvidenceRef,
    InvalidComponentError,
    InvalidProvenanceError,
    InvalidStateError,
    Provenance,
    SchemaConsistencyError,
    Subsystem,
    UnsupportedVersionError,
    UpstreamVersions,
    decision_id_for,
    owner_of,
    section_for,
)


def _upstream():
    return UpstreamVersions(prediction_model_version="pred-1", outcome_model_version="out-1",
                            feature_version="feat-1", embedding_version="sim-emb-1",
                            learning_version="lrn-1", dataset_version="lds-1")


# --------------------------------------------------------------- creation / defaults
def test_create_default_is_empty_placeholder():
    di = DecisionIntelligence.create(prediction_id="p1")
    assert di.status is DecisionStatus.EMPTY and di.is_placeholder
    assert di.decision_intelligence_version == DECISION_INTELLIGENCE_VERSION
    # exactly the four contributor sections, ordered by section name
    assert tuple(c.subsystem for c in di.components) == tuple(sorted(CONTRIBUTORS, key=lambda s: s.value))
    assert all(c.payload is None for c in di.components)
    assert di.evidence_graph is None and di.narrative is None and di.composite_confidence is None


def test_records_upstream_versions():
    di = DecisionIntelligence.create(prediction_id="p1", upstream_versions=_upstream())
    uv = di.upstream_versions
    assert uv.prediction_model_version == "pred-1" and uv.embedding_version == "sim-emb-1"
    assert uv.learning_version == "lrn-1" and uv.dataset_version == "lds-1"


# --------------------------------------------------------------- deterministic / immutable id
def test_decision_id_deterministic_not_random():
    a = DecisionIntelligence.create(prediction_id="p1")
    b = DecisionIntelligence.create(prediction_id="p1")
    assert a.decision_id == b.decision_id == decision_id_for("p1")
    assert DecisionIntelligence.create(prediction_id="p2").decision_id != a.decision_id


def test_identifiers_are_immutable():
    di = DecisionIntelligence.create(prediction_id="p1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        di.decision_id = "x"          # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        di.prediction_id = "y"        # type: ignore[misc]


def test_tampered_decision_id_rejected():
    with pytest.raises(SchemaConsistencyError):
        DecisionIntelligence(
            decision_id="not-deterministic", prediction_id="p1",
            decision_intelligence_version=DECISION_INTELLIGENCE_VERSION, status=DecisionStatus.EMPTY,
            upstream_versions=UpstreamVersions(),
            components=tuple(DecisionComponent.placeholder(s) for s in CONTRIBUTORS),
            checksum="c", provenance=Provenance(subsystem=Subsystem.DECISION_INTELLIGENCE),
        )


# --------------------------------------------------------------- canonical states
def test_all_canonical_states_supported():
    assert {s.value for s in DecisionStatus} == {
        "EMPTY", "INSUFFICIENT_DATA", "PARTIAL", "COMPLETE", "STALE", "ERROR"}
    for st in DecisionStatus:
        assert DecisionIntelligence.create(prediction_id="p1", status=st).status is st


def test_invalid_state_rejected():
    with pytest.raises(InvalidStateError):
        DecisionIntelligence.create(prediction_id="p1", status="DONE")   # type: ignore[arg-type]


# --------------------------------------------------------------- composition contract
def test_composition_contract_maps_each_contributor_to_own_section():
    assert set(COMPOSITION_CONTRACT) == set(CONTRIBUTORS)
    for sub in CONTRIBUTORS:
        assert section_for(sub) == sub.value and owner_of(sub.value) is sub
    assert Subsystem.DECISION_INTELLIGENCE not in COMPOSITION_CONTRACT   # composer owns no section


def test_component_cannot_claim_another_subsystems_section():
    # Learning subsystem trying to own the 'similarity' section → rejected.
    with pytest.raises(InvalidComponentError):
        DecisionComponent(
            subsystem=Subsystem.LEARNING, section="similarity", status=DecisionStatus.EMPTY,
            provenance=Provenance(subsystem=Subsystem.LEARNING),
        )


def test_component_provenance_subsystem_must_match():
    with pytest.raises(InvalidProvenanceError):
        DecisionComponent(
            subsystem=Subsystem.LEARNING, section="learning", status=DecisionStatus.EMPTY,
            provenance=Provenance(subsystem=Subsystem.SIMILARITY),   # mismatched provenance
        )


def test_missing_or_extra_sections_rejected():
    three = tuple(DecisionComponent.placeholder(s) for s in CONTRIBUTORS[:3])
    with pytest.raises(SchemaConsistencyError):
        DecisionIntelligence.create(prediction_id="p1", components=three)   # missing learning
    dup = tuple(DecisionComponent.placeholder(s) for s in CONTRIBUTORS) + \
        (DecisionComponent.placeholder(Subsystem.PREDICTION),)
    with pytest.raises(SchemaConsistencyError):
        DecisionIntelligence.create(prediction_id="p1", components=dup)     # duplicate prediction


# --------------------------------------------------------------- provenance (required always)
def test_placeholder_still_carries_provenance():
    comp = DecisionComponent.placeholder(Subsystem.SIMILARITY, subsystem_version="sim-search-1")
    assert comp.payload is None and comp.status is DecisionStatus.EMPTY
    assert isinstance(comp.provenance, Provenance) and comp.provenance.subsystem is Subsystem.SIMILARITY
    assert comp.provenance.subsystem_version == "sim-search-1"
    # object provenance references the composer itself
    di = DecisionIntelligence.create(prediction_id="p1")
    assert di.provenance.subsystem is Subsystem.DECISION_INTELLIGENCE and di.provenance.source == "p1"


def test_provenance_requires_a_subsystem():
    with pytest.raises(InvalidProvenanceError):
        Provenance(subsystem="learning")     # type: ignore[arg-type]


def test_evidence_ref_requires_kind_and_id():
    ok = EvidenceRef(kind="pattern", ref_id="abc", subsystem=Subsystem.LEARNING)
    assert ok.ref_id == "abc"
    with pytest.raises(InvalidComponentError):
        EvidenceRef(kind="", ref_id="abc", subsystem=Subsystem.LEARNING)


# --------------------------------------------------------------- checksum
def test_checksum_present_and_deterministic():
    a = DecisionIntelligence.create(prediction_id="p1", upstream_versions=_upstream())
    b = DecisionIntelligence.create(prediction_id="p1", upstream_versions=_upstream())
    assert a.checksum and len(a.checksum) == 64        # sha256 hex, present
    assert a.checksum == b.checksum                    # deterministic
    assert DecisionIntelligence.create(prediction_id="p1", status=DecisionStatus.PARTIAL).checksum != a.checksum
    assert DecisionIntelligence.create(prediction_id="p2", upstream_versions=_upstream()).checksum != a.checksum


# --------------------------------------------------------------- version metadata
def test_unsupported_version_rejected():
    with pytest.raises(UnsupportedVersionError):
        DecisionIntelligence.create(prediction_id="p1", version="di-999")


# --------------------------------------------------------------- serialization round-trips
def test_dict_round_trip():
    di = DecisionIntelligence.create(prediction_id="p1", status=DecisionStatus.PARTIAL,
                                     upstream_versions=_upstream())
    got = DecisionIntelligence.from_dict(di.to_dict())
    assert got == di and got.checksum == di.checksum and got.stable_dict() == di.stable_dict()


def test_row_round_trip():
    di = DecisionIntelligence.create(prediction_id="p1", upstream_versions=_upstream())
    got = DecisionIntelligence.from_row(di.to_row())
    assert got.decision_id == di.decision_id and got.stable_dict() == di.stable_dict()
    assert tuple(c.section for c in got.components) == tuple(c.section for c in di.components)


def test_component_and_provenance_round_trip():
    ev = EvidenceRef(kind="recommendation", ref_id="r1", subsystem=Subsystem.LEARNING, note="n")
    prov = Provenance(subsystem=Subsystem.LEARNING, source="p1", subsystem_version="lrn-1",
                      confidence=0.5, evidence_ref=ev)
    comp = DecisionComponent(subsystem=Subsystem.LEARNING, section="learning",
                             status=DecisionStatus.INSUFFICIENT_DATA, provenance=prov, evidence=(ev,))
    assert DecisionComponent.from_dict(comp.to_dict()) == comp
    assert Provenance.from_dict(prov.to_dict()) == prov
    assert EvidenceRef.from_dict(ev.to_dict()) == ev


# --------------------------------------------------------------- immutability / accessors
def test_object_is_frozen():
    di = DecisionIntelligence.create(prediction_id="p1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        di.status = DecisionStatus.COMPLETE      # type: ignore[misc]


def test_component_accessor():
    di = DecisionIntelligence.create(prediction_id="p1")
    assert di.component(Subsystem.LEARNING).section == "learning"
    assert di.component(Subsystem.PREDICTION).subsystem is Subsystem.PREDICTION


# --------------------------------------------------------------- isolation
def test_models_module_imports_no_engine_or_prior_sprint():
    import ast

    import app.decision_intelligence.models as m
    with open(m.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_prefixes = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.memory",
                          "app.similarity", "app.learning", "app.forward_testing")
    for name in imported:
        assert not name.startswith(forbidden_prefixes), f"M1 must not import {name}"
