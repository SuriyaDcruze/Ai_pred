# ADR 0005 — A single `prediction_history.db`

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 1)
- **Deciders:** Architecture / CTO

## Context
Forward Testing needs durable storage, and several future engines (Historical Memory,
Learning, Similarity, GPT history, Model Registry) will need the *same* records. An early
sketch proposed a dedicated `forward.db`. Splitting storage per feature would fragment the
platform's memory, complicate cross-feature queries and joins, and multiply migration and
backup surface — for no benefit at current scale. A separate legacy store (`calls.db`, the
You-vs-AI paper tracker) already exists with a different lifecycle and is left alone.

## Decision
Use **one** permanent store, **`data/prediction_history.db`**, as the single source of
truth for the platform's memory. Schema evolves only through **append-only, versioned,
idempotent migrations** (`app/database/migrations.py`); migration `0001` creates the
`predictions` table with rich context and three independent version stamps from day one.
Future engines add **new tables via new migrations** — never a new database, never an edit
to an existing migration. Raw `sqlite3` + WAL for now; the SQLAlchemy models remain the
future Postgres path (Vol 21).

## Consequences
- **Positive:** one coherent memory; cross-feature queries and joins are trivial; one
  migration history, one backup; forward-compatible readers (`from_row` tolerates missing
  columns) mean additive schema changes never break older code.
- **Positive:** the `predictions` table is written once and treated as immutable except for
  lifecycle columns — an audit-friendly record of what the models actually said.
- **Negative / accepted:** SQLite's single-writer model (mitigated by WAL + a busy timeout +
  an in-process lock); a future high-concurrency need means migrating to Postgres.
- **Revisit when:** concurrency or volume outgrows SQLite — the SQLAlchemy/Postgres path in
  Vol 21 (a new ADR).
