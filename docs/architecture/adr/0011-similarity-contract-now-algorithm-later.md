# ADR 0011 — Similarity: contract now, algorithm later (no fake scores)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 2)
- **Deciders:** Architecture / CTO

## Context
Historical Memory should eventually answer "when did we last see a setup like this?" via
vector similarity over `memory_embeddings`. But the Similarity Engine (Vol 14) is not built,
nothing computes embeddings in Sprint 2, and SQLite has no native vector index. Shipping a
placeholder that returns *some* similarity number would be dishonest — the project's core
value is that it never fabricates a signal.

## Decision
Sprint 2 ships the **contract, not the computation**: `RetrievalEngine.similar()` and
`GET /memory/similar/{id}` validate the prediction exists and then return an explicit
**"Similarity Engine unavailable"** with an empty result set — **never a fabricated score**.
The storage (`memory_embeddings`, vectors `NULL` until populated) and the response shape are
fixed now so Vol 14 can fill them in without an API or schema redesign. When it lands, the
execution model is **filter-then-brute-force** over a pre-filtered candidate set, with any
cap **logged and reported**.

## Consequences
- **Positive:** consumers (GPT, dashboards) can code against the final interface today; the
  day Vol 14 ships, the contract does not change; honesty is preserved — no placeholder
  numbers that could be mistaken for real ones.
- **Positive:** the schema/response shape already anticipates multiple embedding *kinds* and
  a later `sqlite-vss`/pgvector move (ADR 0007).
- **Negative / accepted:** the endpoint is "empty" until Vol 14 — a deliberate, documented
  non-capability rather than a fake one.
- **Enforced by:** tests asserting `available=False`, the exact reason string, and `results
  == []`.
