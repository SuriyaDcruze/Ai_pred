"""Decision Intelligence Engine — read-only composition of the four prior engines (Sprint 5).

The Decision Intelligence Engine assembles, for a given prediction, a single **explainable,
evidence-bound** Decision Intelligence object from the four Sprint 1–4 read surfaces — the stored
Prediction/Outcome/Risk verdict, Historical Memory context, Similarity neighbours, and the Learning
Engine's validated observations. It **composes** what those engines already produced — it re-runs
nothing, recomputes no statistics, imports neither the Prediction nor the Outcome engine, and
modifies nothing upstream. Where history is thin it degrades gracefully and says so
(`INSUFFICIENT_DATA`); it manufactures no edge and emits no advice.

**Distinct from** the legacy live-analysis intelligence (`app/intelligence.py`, `app/sector.py`,
Vol 08), which computes a fresh view from market data + the models directly — a separate concern.

**Current state (Milestone 1):** the **domain model & composition contract** only — the canonical
Decision Intelligence object, its states, provenance, versioning, the per-subsystem section
contract, and validation. Every section is a placeholder; the composition engine, evidence graph,
narrative, confidence, and REST API arrive in later milestones.
"""

from __future__ import annotations

from app.decision_intelligence.models import (
    COMPOSITION_CONTRACT,
    CONTRIBUTORS,
    DECISION_INTELLIGENCE_VERSION,
    SECTION_OWNER,
    DecisionComponent,
    DecisionIntelligence,
    DecisionIntelligenceError,
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

__all__ = [
    "DECISION_INTELLIGENCE_VERSION",
    "DecisionStatus",
    "Subsystem",
    "CONTRIBUTORS",
    "COMPOSITION_CONTRACT",
    "SECTION_OWNER",
    "section_for",
    "owner_of",
    "EvidenceRef",
    "Provenance",
    "UpstreamVersions",
    "DecisionComponent",
    "DecisionIntelligence",
    "decision_id_for",
    # errors
    "DecisionIntelligenceError",
    "InvalidStateError",
    "InvalidProvenanceError",
    "InvalidComponentError",
    "SchemaConsistencyError",
    "UnsupportedVersionError",
]
