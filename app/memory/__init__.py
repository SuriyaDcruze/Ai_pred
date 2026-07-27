"""Historical Memory — Aegis' permanent knowledge layer over completed predictions.

Historical Memory turns each completed prediction into structured, retrievable historical
knowledge. It is built as a **read + enrich + index + retrieve** layer over the existing
``predictions`` table plus a small set of **satellite tables** in the *same*
``prediction_history.db`` — never a second database and never a copy of ``predictions``.

It **stores facts; it never creates them**: it performs no inference, never retrains a model,
and never modifies a prediction's results. It is strictly independent of the Prediction and
Outcome engines (it imports neither).

**Current state (Milestones 1–2):** the database foundation (satellite schema via
append-only migrations + domain models) and the **Memory Store** — thread-safe, idempotent
CRUD over the satellite tables. The Memory Builder, Retrieval Engine and REST API arrive in
later milestones.
"""

from __future__ import annotations

from app.memory.errors import (
    MemoryConflictError,
    MemoryForeignKeyError,
    MemoryNotFoundError,
    MemorySchemaError,
    MemoryStoreError,
)
from app.memory.models import (
    DEFAULT_EMBEDDING_KIND,
    MEMORY_SCHEMA_VERSION,
    AggregateDimension,
    MemoryAggregate,
    MemoryEmbedding,
    MemoryReasoning,
    pack_vector,
    unpack_vector,
)
from app.memory.store import MemoryStore

__all__ = [
    # models
    "MemoryReasoning",
    "MemoryEmbedding",
    "MemoryAggregate",
    "AggregateDimension",
    "MEMORY_SCHEMA_VERSION",
    "DEFAULT_EMBEDDING_KIND",
    "pack_vector",
    "unpack_vector",
    # store
    "MemoryStore",
    # errors
    "MemoryStoreError",
    "MemoryNotFoundError",
    "MemoryConflictError",
    "MemoryForeignKeyError",
    "MemorySchemaError",
]
