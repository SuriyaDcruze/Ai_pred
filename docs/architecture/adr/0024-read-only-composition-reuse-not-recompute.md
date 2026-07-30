# ADR 0024 — Read-only composition: reuse, never recompute

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 5)
- **Deciders:** Architecture / CTO

## Context
The Decision Intelligence Engine (ADR 0023) sits at the end of the chain and consumes every prior
stage's output. Two failure modes must be designed out: (a) it could **mutate** something upstream
(a prediction, a memory fact, an embedding, a learning artifact) and break the immutability the
earlier sprints depend on; (b) it could **re-implement** another engine's maths (win rate, CIs,
similarity, recommendations) and quietly **drift** from the source of truth — a second, contradicting
number for the same thing.

## Decision
The engine is **strictly read-only** and **reuses each engine's output verbatim** — it recomputes
**no** statistic. It reads the stored Prediction/Outcome/Risk verdict via `PredictionStore`,
Historical Memory via `RetrievalEngine`, neighbours via the Similarity read path, and the Learning
Engine's observations via a **provider that runs the Learning Engine's own pipeline** (never a
re-implementation). Every composed figure equals its source engine's figure. It **writes nothing**
(compose-on-read; ADR 0028), changes no prior table, adds no migration, and imports neither the
Prediction nor the Outcome engine. A change to an upstream method is a new **upstream** version, not
a re-implementation here.

## Alternatives considered
- *Re-derive stats for speed/independence* — rejected: creates a drifting second source of truth;
  reuse the owning engine's output instead (asserted equal by a regression test).
- *Invoke the Outcome/Prediction engine to (re)score* — rejected: it must use the **stored**
  outputs verbatim (ADR 0002/0003/0018); re-invoking couples read analytics to a live model.
- *Let composition denormalise into memory tables* — rejected: that is a write into Sprint 2's
  tables; determinism makes recompute-on-read equivalent at current scale.

## Consequences
- **Positive:** the composition can never damage or contradict an upstream engine; every number is
  traceable to its owner; rollback is trivial (it stores nothing).
- **Negative / accepted:** recompute-on-read repeats work per request (bounded at current volumes; a
  cache is a future option — ADR 0027/0028).
- **Enforced by:** AST no-engine-import guards; no-write tests; a reuse-regression test (composed
  figures equal `compute_aggregates`/the Learning Engine's own output); unchanged-Sprint-1–4 tests.
