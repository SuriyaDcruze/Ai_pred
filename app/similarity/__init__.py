"""Similarity — the "I have seen this setup before" engine (Volume 14, Sprint 3).

The Similarity Engine will let Aegis retrieve the historical decisions most like a given one,
to power explainability ("similar setups won X%") and grounding for the GPT assistant. It
consumes Historical Memory (Sprint 2) and fills the `memory_embeddings` placeholder; it
performs **no** prediction and modifies neither Historical Memory nor the Prediction/Outcome
engines.

**Current state (Milestones 1–3):** the **Feature Vector Builder** (Memory Record → versioned
numerical vector), the **Embedding Generator** (deterministic L2-normalised embedding stored in
``memory_embeddings``), and the **Similarity Search Engine** — read-only cosine k-NN over those
embeddings with honest neighbour statistics. It does *not* yet expose an API or wire into the
Retrieval Engine; those are later milestones.
"""

from __future__ import annotations

from app.similarity.embedding import (
    EMBEDDING_KIND,
    EMBEDDING_SCHEMA_VERSION,
    EMBEDDING_VERSION,
    EmbeddingBackfillSummary,
    EmbeddingGenerator,
    l2_normalize,
)
from app.similarity.feature_vector import (
    FEATURE_VERSION,
    SCHEMA_VERSION,
    VECTOR_DIM,
    FeatureVectorBuilder,
    feature_layout,
)
from app.similarity.models import (
    DimensionMismatchError,
    Embedding,
    FeatureVector,
    InvalidFeatureVectorError,
    InvalidMemoryRecordError,
    MissingEmbeddingError,
    MissingFieldError,
    SearchRequestError,
    SimilarityError,
    UnsupportedVersionError,
)
from app.similarity.search import (
    METRIC,
    SIMILARITY_VERSION,
    SimilarityFilter,
    SimilarityNeighbour,
    SimilaritySearchEngine,
    SimilaritySearchResult,
    SimilaritySummary,
    cosine_similarity,
)

__all__ = [
    # feature vectors (M1)
    "FeatureVectorBuilder",
    "FeatureVector",
    "FEATURE_VERSION",
    "SCHEMA_VERSION",
    "VECTOR_DIM",
    "feature_layout",
    # embeddings (M2)
    "EmbeddingGenerator",
    "Embedding",
    "EmbeddingBackfillSummary",
    "EMBEDDING_VERSION",
    "EMBEDDING_SCHEMA_VERSION",
    "EMBEDDING_KIND",
    "l2_normalize",
    # search (M3)
    "SimilaritySearchEngine",
    "SimilarityFilter",
    "SimilarityNeighbour",
    "SimilaritySummary",
    "SimilaritySearchResult",
    "cosine_similarity",
    "SIMILARITY_VERSION",
    "METRIC",
    # errors
    "SimilarityError",
    "InvalidMemoryRecordError",
    "MissingFieldError",
    "UnsupportedVersionError",
    "DimensionMismatchError",
    "InvalidFeatureVectorError",
    "MissingEmbeddingError",
    "SearchRequestError",
]
