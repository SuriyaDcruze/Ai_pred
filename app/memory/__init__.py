"""Historical Memory — Aegis' permanent knowledge layer over completed predictions.

Historical Memory turns each completed prediction into structured, retrievable historical
knowledge. It is built as a **read + enrich + index + retrieve** layer over the existing
``predictions`` table plus a small set of **satellite tables** in the *same*
``prediction_history.db`` — never a second database and never a copy of ``predictions``.

It **stores facts; it never creates them**: it performs no inference, never retrains a model,
and never modifies a prediction's results. It is strictly independent of the Prediction and
Outcome engines (it imports neither).

**Current state (Milestones 1–3):** the database foundation (satellite schema + models), the
**Memory Store** (thread-safe, idempotent CRUD over the satellite tables), and the **Memory
Builder** — enriches completed predictions into reasoning + embedding-placeholder rows,
maintains derived aggregates, and backfills. The Retrieval Engine and REST API arrive in
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
from app.memory.aggregates import compute_aggregates, confidence_bucket
from app.memory.builder import BackfillSummary, BuildStatus, MemoryBuilder
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
    # builder + aggregates
    "MemoryBuilder",
    "BuildStatus",
    "BackfillSummary",
    "compute_aggregates",
    "confidence_bucket",
    # errors
    "MemoryStoreError",
    "MemoryNotFoundError",
    "MemoryConflictError",
    "MemoryForeignKeyError",
    "MemorySchemaError",
]
