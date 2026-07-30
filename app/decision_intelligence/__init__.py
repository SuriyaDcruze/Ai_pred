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

**Current state (Milestones 1–4):** the **domain model & composition contract** (M1), the
**Composition Engine** (M2), the **Evidence & Explanation Engine** (M3), and the **Composite
Confidence & Prioritisation Engine** (M4): a deterministic, read-only **evidence-quality** indicator
(how trustworthy/complete/consistent the assembled evidence is — **never** a probability of success
or a trading signal) + conflict detection + a prioritisation score that organises objects by
evidence strength only. The REST API arrives in a later milestone.
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
from app.decision_intelligence.compose import (
    CompositionEngine,
    CompositionError,
    LearningSource,
    LearningView,
    MemorySource,
    MissingPredictionError,
    PredictionSource,
    SimilaritySource,
    SourceAdapter,
    build_engine,
)
from app.decision_intelligence.evidence import (
    DuplicateEvidenceError,
    EvidenceEngine,
    EvidenceGraph,
    EvidenceNode,
    ExplainedDecision,
    Explanation,
    ExplanationError,
    ForAgainstItem,
    MissingEvidenceItem,
    MissingReason,
    OrphanedEvidenceError,
    Stance,
)
from app.decision_intelligence.confidence import (
    CompositeConfidence,
    ConfidenceEngine,
    ConfidenceError,
    ConfidenceFactor,
    ConfidenceLevel,
    Conflict,
    ConflictKind,
    EvidenceQuality,
    InvalidConfidenceError,
    Penalty,
    Strength,
    prioritise,
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
    # composition (M2)
    "CompositionEngine",
    "build_engine",
    "SourceAdapter",
    "PredictionSource",
    "MemorySource",
    "SimilaritySource",
    "LearningSource",
    "LearningView",
    # evidence & explanation (M3)
    "EvidenceEngine",
    "EvidenceGraph",
    "EvidenceNode",
    "ExplainedDecision",
    "Explanation",
    "ForAgainstItem",
    "MissingEvidenceItem",
    "Stance",
    "MissingReason",
    # composite confidence & prioritisation (M4)
    "ConfidenceEngine",
    "CompositeConfidence",
    "ConfidenceFactor",
    "ConfidenceLevel",
    "Conflict",
    "ConflictKind",
    "Penalty",
    "Strength",
    "EvidenceQuality",
    "prioritise",
    # errors
    "DecisionIntelligenceError",
    "CompositionError",
    "MissingPredictionError",
    "ExplanationError",
    "OrphanedEvidenceError",
    "DuplicateEvidenceError",
    "ConfidenceError",
    "InvalidConfidenceError",
    "InvalidStateError",
    "InvalidProvenanceError",
    "InvalidComponentError",
    "SchemaConsistencyError",
    "UnsupportedVersionError",
]
