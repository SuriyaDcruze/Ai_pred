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

**Current state (Milestones 1–4):** the **Learning Dataset Builder** (deterministic, read-only
view of completed decisions), the **Pattern Extraction Engine** (groups the dataset into
deterministic candidate patterns — descriptive only), the **Statistical Validation Engine**
(descriptive statistics + confidence intervals + significance + multiple-comparison correction →
`VALIDATED`/`HYPOTHESIS`/`INSUFFICIENT_DATA`, reusing the Sprint 2 aggregate math), and the
**Recommendation Engine** (turns VALIDATED patterns into evidence-bound **descriptive**
recommendation objects — never advice, never a prediction). The REST API arrives in a later
milestone.
"""

from __future__ import annotations

from app.learning.dataset import LearningDatasetBuilder
from app.learning.models import (
    DATASET_VERSION,
    DEFAULT_MIN_CORPUS,
    LEARNING_VERSION,
    CandidatePattern,
    ConfidenceInterval,
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
    InvalidValidationError,
    LearningStatus,
    MalformedPatternError,
    MissingEvidenceError,
    PatternExtractionResult,
    Recommendation,
    RecommendationConfidence,
    RecommendationResult,
    RecommendationType,
    Significance,
    StatisticsError,
    UnknownCorrectionError,
    UnknownDimensionError,
    UnsupportedVersionError,
    ValidatedPattern,
    ValidationResult,
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
from app.learning.statistics import (
    CORRECTION_STRATEGIES,
    DEFAULT_ALPHA,
    DEFAULT_BASELINE,
    DEFAULT_CORRECTION,
    DEFAULT_MIN_SAMPLE,
    DEFAULT_PERIODS,
    StatisticalValidator,
    available_corrections,
    ci_quality,
    consistency_score,
    proportion_ztest,
    wilson_interval,
)
from app.learning.recommendations import (
    CATEGORY_BY_DIMENSION,
    UNSTABLE_THRESHOLD,
    RecommendationEngine,
    recommendation_category,
    recommendation_confidence,
    recommendation_type_of,
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
    # statistical validation (M3)
    "StatisticalValidator",
    "ValidatedPattern",
    "ValidationResult",
    "ConfidenceInterval",
    "Significance",
    "CORRECTION_STRATEGIES",
    "available_corrections",
    "wilson_interval",
    "ci_quality",
    "proportion_ztest",
    "consistency_score",
    "DEFAULT_MIN_SAMPLE",
    "DEFAULT_ALPHA",
    "DEFAULT_BASELINE",
    "DEFAULT_CORRECTION",
    "DEFAULT_PERIODS",
    # recommendations (M4)
    "RecommendationEngine",
    "Recommendation",
    "RecommendationResult",
    "RecommendationType",
    "RecommendationConfidence",
    "recommendation_category",
    "recommendation_type_of",
    "recommendation_confidence",
    "CATEGORY_BY_DIMENSION",
    "UNSTABLE_THRESHOLD",
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
    "MalformedPatternError",
    "StatisticsError",
    "UnknownCorrectionError",
    "InvalidValidationError",
    "MissingEvidenceError",
]
