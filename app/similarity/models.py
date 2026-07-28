"""Domain models + typed errors for the Similarity feature layer (Sprint 3 · Vol 14 · M1).

This module defines the **output** of the Feature Vector Builder — an immutable, versioned
:class:`FeatureVector` — and the typed errors the builder raises. It contains no encoding
logic (that lives in :mod:`app.similarity.feature_vector`) and imports nothing from the
Prediction or Outcome engines, or from Historical Memory: the builder consumes a Memory
Record *contract* (a plain mapping), never the engines.
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------------------------------------- errors
class SimilarityError(Exception):
    """Base class for every error raised by the Similarity feature layer."""


class InvalidMemoryRecordError(SimilarityError):
    """The input is not a well-formed Memory Record (wrong type or unusable shape)."""


class MissingFieldError(InvalidMemoryRecordError):
    """A field required to build a feature vector was absent from the Memory Record."""


class UnsupportedVersionError(SimilarityError):
    """The requested feature version, or the record's schema version, is not supported."""


class DimensionMismatchError(SimilarityError):
    """A feature vector's dimension does not match the expected feature-schema dimension."""


class InvalidFeatureVectorError(SimilarityError):
    """A feature vector is malformed (e.g. contains NaN/inf) and cannot be embedded."""


# --------------------------------------------------------------------------- vector
@dataclass(frozen=True)
class FeatureVector:
    """A deterministic numerical representation of one Memory Record.

    Immutable and self-describing: it carries the ``feature_version`` (the encoding
    contract), the ``schema_version`` (the feature-schema revision), and the ``dimension``,
    so a downstream Similarity Engine can refuse to compare vectors of different versions or
    dimensions. ``values`` is a tuple (hashable, comparable) of length ``dimension``.
    """

    values: tuple[float, ...]
    feature_version: str
    schema_version: int
    dimension: int

    def __post_init__(self) -> None:
        if len(self.values) != self.dimension:
            raise SimilarityError(
                f"vector length {len(self.values)} != declared dimension {self.dimension}"
            )

    def to_list(self) -> list[float]:
        """Return the feature values as a plain list (e.g. for later embedding/storage)."""
        return list(self.values)


@dataclass(frozen=True)
class Embedding:
    """A deterministic embedding of a feature vector, ready to store in ``memory_embeddings``.

    Immutable and self-describing. Because the Sprint 2 ``memory_embeddings`` table is frozen
    and has no dedicated version columns, ``embedding_version`` + ``feature_version`` are
    persisted together in the row's ``model_name`` (``"<embedding_version>/<feature_version>"``);
    ``dimension`` maps to ``dim`` and ``schema_version`` to the row's ``schema_version``.
    """

    vector: tuple[float, ...]
    embedding_version: str
    feature_version: str
    schema_version: int
    dimension: int
    embedding_kind: str
    created_at: str
    prediction_id: str | None = None

    def __post_init__(self) -> None:
        if len(self.vector) != self.dimension:
            raise SimilarityError(
                f"embedding length {len(self.vector)} != declared dimension {self.dimension}"
            )

    def to_list(self) -> list[float]:
        """Return the embedding values as a plain list."""
        return list(self.vector)
