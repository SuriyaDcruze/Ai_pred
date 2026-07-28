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
