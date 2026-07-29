"""Behavioural Learning Engine — descriptive analytics over completed Historical Memory (Vol 15).

The Behavioural Learning Engine turns **completed** historical decisions into statistically
honest, explainable observations — patterns, validated statistics, and evidence-bound
recommendations — that later stages (Decision Intelligence, the GPT assistant) can explain. It
is **descriptive, not prescriptive**: it performs **no model training, no inference, no
prediction**, and it modifies nothing upstream (predictions, Historical Memory, Similarity are
read-only). Where history is thin it says so (`INSUFFICIENT_DATA`) rather than manufacturing an
edge.

**Distinct from** the legacy meta-model retrainer in `app/training/` (Volume 15's other sense),
which *does* train models via the validated promotion pipeline — a separate subsystem.

**Current state (Milestones 1–2):** the **Learning Dataset Builder** (deterministic, read-only
view of completed decisions) and the **Pattern Extraction Engine** (groups the dataset into
deterministic candidate patterns — descriptive only, `HYPOTHESIS`/`INSUFFICIENT_DATA`, no
statistics). Statistical validation, recommendations and the REST API arrive in later
milestones.
"""

from __future__ import annotations

from app.learning.dataset import LearningDatasetBuilder
from app.learning.models import (
    DATASET_VERSION,
    DEFAULT_MIN_CORPUS,
    LEARNING_VERSION,
    CandidatePattern,
    CorruptedMetadataError,
    DuplicatePatternError,
    IncompleteOutcomeError,
    InconsistentEvidenceError,
    InconsistentTimestampError,
    InvalidDatasetError,
    InvalidMemoryRecordError,
    LearningDataset,
    LearningError,
    LearningRecord,
    LearningRun,
    LearningStatus,
    PatternExtractionResult,
    UnknownDimensionError,
    UnsupportedVersionError,
    checksum_of,
)
from app.learning.patterns import (
    DEFAULT_MIN_EVIDENCE,
    PATTERN_DIMENSIONS,
    PatternExtractor,
    available_dimensions,
    confidence_bucket,
    holding_bucket,
)

__all__ = [
    # dataset builder (M1)
    "LearningDatasetBuilder",
    "LearningDataset",
    "LearningRecord",
    "LearningRun",
    "LearningStatus",
    "checksum_of",
    "LEARNING_VERSION",
    "DATASET_VERSION",
    "DEFAULT_MIN_CORPUS",
    # pattern extraction (M2)
    "PatternExtractor",
    "CandidatePattern",
    "PatternExtractionResult",
    "PATTERN_DIMENSIONS",
    "available_dimensions",
    "confidence_bucket",
    "holding_bucket",
    "DEFAULT_MIN_EVIDENCE",
    # errors
    "LearningError",
    "InvalidMemoryRecordError",
    "IncompleteOutcomeError",
    "InconsistentTimestampError",
    "UnsupportedVersionError",
    "CorruptedMetadataError",
    "InvalidDatasetError",
    "UnknownDimensionError",
    "DuplicatePatternError",
    "InconsistentEvidenceError",
]
