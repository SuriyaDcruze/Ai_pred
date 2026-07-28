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

**Current state (Milestone 1):** only the **Learning Dataset Builder** — a deterministic,
read-only view of completed decisions, plus the canonical learning states and the learning
storage foundation. Pattern extraction, statistics, recommendations and the REST API arrive in
later milestones.
"""

from __future__ import annotations

from app.learning.dataset import LearningDatasetBuilder
from app.learning.models import (
    DATASET_VERSION,
    DEFAULT_MIN_CORPUS,
    LEARNING_VERSION,
    CorruptedMetadataError,
    IncompleteOutcomeError,
    InconsistentTimestampError,
    InvalidMemoryRecordError,
    LearningDataset,
    LearningError,
    LearningRecord,
    LearningRun,
    LearningStatus,
    UnsupportedVersionError,
    checksum_of,
)

__all__ = [
    # builder
    "LearningDatasetBuilder",
    # models
    "LearningDataset",
    "LearningRecord",
    "LearningRun",
    "LearningStatus",
    "checksum_of",
    "LEARNING_VERSION",
    "DATASET_VERSION",
    "DEFAULT_MIN_CORPUS",
    # errors
    "LearningError",
    "InvalidMemoryRecordError",
    "IncompleteOutcomeError",
    "InconsistentTimestampError",
    "UnsupportedVersionError",
    "CorruptedMetadataError",
]
