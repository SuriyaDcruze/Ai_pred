# ADR 0008 — The Memory Record is composed on read

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 2)
- **Deciders:** Architecture / CTO

## Context
Given the satellite-table design (ADR 0007), a full **Memory Record** — prediction + outcome
+ reasoning + embedding slot + aggregate summary + metadata — is spread across `predictions`
and several satellites. Consumers (Decision Intelligence, GPT, analytics) want it as one
object. The tempting shortcut is to materialise a denormalised copy; that reintroduces a
second source of truth and a dual-write consistency problem.

## Decision
Assemble the Memory Record **dynamically on read** in the Retrieval Engine: fetch the
prediction (via `PredictionStore`) and its satellites (via `MemoryStore`), and merge them
into a `MemoryRecord` whose `to_dict()` surfaces the canonical flat view. The prediction is
**embedded** as the single source of truth — no prediction field is copied into a stored
second place. A **missing satellite yields `null`/defaults, never an error** (the common
early state, when little memory has been built).

## Consequences
- **Positive:** exactly one source of truth per field; an entire class of consistency bugs
  disappears; storage duplication avoided; the record shape can evolve (`schema_version` in
  metadata) without a data migration.
- **Positive:** works from day one on a near-empty store — records compose with `null`
  reasoning/embedding rather than failing.
- **Negative / accepted:** each record read does a small fan-out of satellite lookups
  (cheap at our scale; the page, not the whole corpus, is composed).
- **Enforced by:** retrieval tests for composition with and without satellites, and a
  "no-writes" test proving retrieval never mutates anything.
