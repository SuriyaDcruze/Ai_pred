# ADR 0007 — Satellite-table architecture for Historical Memory

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 2)
- **Deciders:** Architecture / CTO

## Context
Historical Memory must turn each completed prediction into an enriched, retrievable record
covering reasoning, embeddings, and derived aggregates — while reusing the one
`prediction_history.db`, extending schema only via migrations, and **never** modifying the
Sprint 1 `predictions` table (ADR 0005). Of the ~22 fields a Memory Record needs, 17 already
live on `predictions`.

## Decision
Store only what `predictions` does **not** already hold, in **satellite tables** keyed on
`prediction_id`, added by append-only migrations:

- `memory_reasoning` (1:1) — rationale, factors, rule-check, a mirrored `confidence`.
- `memory_embeddings` (0..n by `embedding_kind`) — vector placeholder for Similarity.
- `memory_aggregates` — derived rollups keyed `(dimension, bucket, model_version)`.

Plus additive **indexes** on `predictions` for retrieval (indexes are metadata; they change
no row). No fat `memory_records` table duplicating prediction fields; no new columns on
`predictions`.

## Consequences
- **Positive:** one source of truth per field (no dual-write drift); `predictions` stays
  immutable and untouched; every change is purely additive; rollback is a clean table/index
  drop with zero impact on Sprint 1.
- **Positive:** each satellite evolves independently (its own `schema_version`); embeddings
  (the storage hog) sit in their own droppable/recomputable table.
- **Negative / accepted:** a Memory Record must be **composed on read** (a join), not read
  from one row — addressed by ADR 0008.
- **Enforced by:** migration tests (fresh DB, populated-Sprint-1 upgrade, idempotency,
  rollback) proving `predictions` byte-for-byte unchanged.
