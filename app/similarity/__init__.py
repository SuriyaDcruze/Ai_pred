"""Similarity — the "I have seen this setup before" engine (Volume 14, Sprint 3).

The Similarity Engine will let Aegis retrieve the historical decisions most like a given one,
to power explainability ("similar setups won X%") and grounding for the GPT assistant. It
consumes Historical Memory (Sprint 2) and fills the `memory_embeddings` placeholder; it
performs **no** prediction and modifies neither Historical Memory nor the Prediction/Outcome
engines.

**Current state (Milestone 1):** only the **Feature Vector Builder** — a pure, deterministic
transformation from a Memory Record to a versioned numerical feature vector. It does *not*
generate embeddings, compare vectors, or rank similarity; those are later milestones.
"""

from __future__ import annotations

from app.similarity.feature_vector import (
    FEATURE_VERSION,
    SCHEMA_VERSION,
    VECTOR_DIM,
    FeatureVectorBuilder,
    feature_layout,
)
from app.similarity.models import (
    FeatureVector,
    InvalidMemoryRecordError,
    MissingFieldError,
    SimilarityError,
    UnsupportedVersionError,
)

__all__ = [
    "FeatureVectorBuilder",
    "FeatureVector",
    "FEATURE_VERSION",
    "SCHEMA_VERSION",
    "VECTOR_DIM",
    "feature_layout",
    "SimilarityError",
    "InvalidMemoryRecordError",
    "MissingFieldError",
    "UnsupportedVersionError",
]
