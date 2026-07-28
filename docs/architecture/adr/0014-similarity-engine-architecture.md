# ADR 0014 — Similarity Engine architecture (filter-first brute-force cosine)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 3)
- **Deciders:** Architecture / CTO

## Context
The Similarity Engine must return the k historical decisions most similar to a query, with
honest outcome statistics — over SQLite, which has **no ANN/vector index**. It must be
read-only, deterministic, and never over-claim (the legacy kNN similarity showed **no
predictive edge** — this is explainability, ADR 0011).

## Decision
Implement **`sim-search-1`**: **filter-first, then brute-force cosine**.
1. Narrow candidates by cheap Memory-Record predicates (symbol/sector/timeframe/regime/phase/
   outcome/model/feature version).
2. Cosine-compare each candidate embedding (unit-length → dot product).
3. Threshold by `min_similarity`, exclude the query, dedup, sort **deterministically**
   (`-similarity`, then `prediction_id`), return the top *k*.
4. Report an honest `SimilaritySummary` (sample size, win rate, avg R, outcome distribution).
A **candidate cap** bounds the brute-force set; when it bites it is **logged** (never silent).

## Alternatives considered
- *pgvector / a real ANN index* — deferred: requires Postgres; the corpus is small (near-empty
  live). The schema shape is chosen so this is a future migration, not a redesign (Vol 21).
- *Euclidean distance* — cosine chosen because embeddings are unit-length and direction is the
  meaningful signal.
- *Returning a similarity "score" as a prediction* — rejected: it is not a predictive edge;
  results are framed as explanation only.

## Consequences
- **Positive:** correct + deterministic + read-only; no new infrastructure; honest stats with
  sample size; incompatible-version candidates skipped (logged), not silently dropped.
- **Negative / accepted:** O(candidates) per query — fine at current scale; the cap + pgvector
  path handle growth.
- **Enforced by:** cosine-correctness, ranking/determinism, cap/threshold, empty-corpus, and
  no-write tests; AST guard that the package imports neither engine.
