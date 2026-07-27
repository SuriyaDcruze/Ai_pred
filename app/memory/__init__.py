"""Historical Memory — Aegis' permanent knowledge layer over completed predictions.

Historical Memory turns each completed prediction into structured, retrievable historical
knowledge. It is built as a **read + enrich + index + retrieve** layer over the existing
``predictions`` table plus a small set of **satellite tables** in the *same*
``prediction_history.db`` — never a second database and never a copy of ``predictions``.

It **stores facts; it never creates them**: it performs no inference, never retrains a model,
and never modifies a prediction's results. It is strictly independent of the Prediction and
Outcome engines (it imports neither).

**Current state (Milestones 1–4):** the database foundation (satellite schema + models), the
**Memory Store** (thread-safe, idempotent CRUD), the **Memory Builder** (enrich + aggregates
+ backfill), and the **Retrieval Engine** — composes Memory Records on read, with filtered
search, keyset pagination, aggregate reads, the similarity contract, and a GPT context
bundle. The REST API arrives in a later milestone.
"""

from __future__ import annotations

from app.memory.errors import (
    MemoryConflictError,
    MemoryForeignKeyError,
    MemoryNotFoundError,
    MemoryQueryError,
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
from app.memory.retrieval import (
    MemoryFilter,
    MemoryRecord,
    RetrievalEngine,
    SearchPage,
    SimilarityResult,
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
    # builder + aggregates
    "MemoryBuilder",
    "BuildStatus",
    "BackfillSummary",
    "compute_aggregates",
    "confidence_bucket",
    # retrieval
    "RetrievalEngine",
    "MemoryRecord",
    "MemoryFilter",
    "SearchPage",
    "SimilarityResult",
    # errors
    "MemoryStoreError",
    "MemoryNotFoundError",
    "MemoryConflictError",
    "MemoryForeignKeyError",
    "MemorySchemaError",
    "MemoryQueryError",
]
