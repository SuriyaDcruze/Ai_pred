"""Typed exceptions for the Historical Memory persistence layer (Sprint 2 · Milestone 2).

The Memory Store never silently ignores a failure — it raises one of these, so callers can
react precisely (a missing prediction is different from a duplicate, which is different from
a corrupt schema version). The base is deliberately **not** named ``MemoryError`` to avoid
shadowing the Python builtin of that name.
"""

from __future__ import annotations


class MemoryStoreError(Exception):
    """Base class for every Historical Memory persistence error."""


class MemoryNotFoundError(MemoryStoreError):
    """A record expected to exist (e.g. for an update) was not found."""


class MemoryConflictError(MemoryStoreError):
    """A uniqueness/primary-key constraint was violated (e.g. a strict create of an
    already-existing row, or a duplicate ``(prediction_id, embedding_kind)``)."""


class MemoryForeignKeyError(MemoryStoreError):
    """A satellite row referenced a ``prediction_id`` that does not exist in ``predictions``."""


class MemorySchemaError(MemoryStoreError):
    """A record carried a ``schema_version`` this build does not understand."""


class MemoryQueryError(MemoryStoreError):
    """A retrieval request was malformed — an invalid filter (e.g. ``confidence_min`` >
    ``confidence_max``, an unknown outcome status) or a bad pagination cursor/limit."""
