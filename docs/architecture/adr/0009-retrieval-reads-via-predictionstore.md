# ADR 0009 — Retrieval reads predictions only via PredictionStore

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 2)
- **Deciders:** Architecture / CTO

## Context
The Retrieval Engine must search and filter historical decisions by symbol, timeframe,
regime, sector, model/feature version, confidence, outcome, and date range, with
deterministic pagination. It could issue its own SQL against `predictions` (using the M1
retrieval indexes), but that would make Historical Memory a second writer/reader of Sprint
1's table and blur ownership.

## Decision
Historical Memory reads `predictions` **exclusively through `PredictionStore` read methods**
and filters in application code; it issues **no direct SQL** against `predictions`.
Pagination is **keyset** on a deterministic `(created_at, prediction_id)` DESC ordering (a
base64 cursor), so results are stable and reproducible. Malformed filters/cursors/limits
raise a typed `MemoryQueryError` (→ HTTP 422).

## Consequences
- **Positive:** Sprint 1 keeps sole ownership of its table; Historical Memory cannot
  accidentally write or lock it in an unexpected way; the boundary is simple to enforce and
  test.
- **Positive:** deterministic keyset pagination is correct under insertion and needs no
  server-side cursor state.
- **Negative / accepted:** filtering is O(N) in Python over the returned predictions rather
  than index-accelerated in SQL. Fine at current volumes (hundreds–thousands); the M1
  `predictions` indexes remain forward-investment for a future `PredictionStore` query API or
  the Postgres move.
- **Revisit when:** the corpus grows enough that in-app filtering misses its latency budget —
  then add indexed query methods to `PredictionStore` (a Sprint 1 change) or move to Postgres.
