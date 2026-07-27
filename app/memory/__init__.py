"""Historical Memory — Aegis' permanent knowledge layer over completed predictions.

Historical Memory turns each completed prediction into structured, retrievable historical
knowledge. It is built as a **read + enrich + index + retrieve** layer over the existing
``predictions`` table plus a small set of **satellite tables** in the *same*
``prediction_history.db`` — never a second database and never a copy of ``predictions``.

It **stores facts; it never creates them**: it performs no inference, never retrains a model,
and never modifies a prediction's results. It is strictly independent of the Prediction and
Outcome engines (it imports neither).

**Milestone 1 (this package's current state)** provides only the database foundation: the
satellite schema (via new append-only migrations) and the domain models for the satellite
rows. The Memory Store, Builder, Retrieval Engine and REST API arrive in later milestones.
"""

from __future__ import annotations

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

__all__ = [
    "MemoryReasoning",
    "MemoryEmbedding",
    "MemoryAggregate",
    "AggregateDimension",
    "MEMORY_SCHEMA_VERSION",
    "DEFAULT_EMBEDDING_KIND",
    "pack_vector",
    "unpack_vector",
]
