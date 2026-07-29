# ADR 0018 — The Learning Engine is read-only over everything upstream

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 4)
- **Deciders:** Architecture / CTO

## Context
The Behavioural Learning Engine (ADR 0017) sits at the end of the chain — Forward Testing →
Prediction/Outcome/Risk → Historical Memory → Similarity → **Learning** — and consumes the output
of every prior stage. Anything it could mutate upstream (a prediction, a memory fact, an
aggregate, an embedding, a model artifact, a prior migration) would break the immutability the
earlier sprints depend on and could silently corrupt the "single source of truth" (`predictions`).

## Decision
The Learning Engine is **strictly read-only** with respect to all prior stages. It reads
Historical Memory via `RetrievalEngine`/`MemoryStore` (Memory Records + `memory_aggregates`) and
the Outcome Engine's **already-stored** outputs (verbatim, via the records — never invoking it).
It **writes only its own learning tables** and, through Milestone 5, wrote **nothing at all** (the
dataset builder, extractor, validator, recommender, and API are pure functions over their inputs;
the learning tables are provisioned but not yet populated). It imports **neither** the Prediction
nor the Outcome engine, changes **no** prior table, and adds only **append-only** migrations.

## Alternatives considered
- *Let Learning snapshot/denormalise into memory tables for speed* — rejected: that is a write
  into Sprint 2's tables; determinism + recompute-on-read make it unnecessary at current scale.
- *Have Learning call the Outcome Engine to (re)score* — rejected: it must use the stored,
  historical outputs verbatim; re-invoking risks drift and couples read analytics to a model.

## Consequences
- **Positive:** the analytics layer can never damage predictions, memory, embeddings, or models;
  rollback of any learning artifact is a clean table drop; earlier sprints stay provably intact.
- **Negative / accepted:** recompute-on-read repeats work each request (fine at current volumes;
  a cached last-run is a future option) — see ADR 0020.
- **Enforced by:** AST no-engine-import guards; no-write tests (learning tables stay empty);
  migration tests asserting `predictions` (and every prior table) is byte-for-byte unchanged on
  upgrade; the unchanged Sprint 1–3 suites re-run green each milestone.
