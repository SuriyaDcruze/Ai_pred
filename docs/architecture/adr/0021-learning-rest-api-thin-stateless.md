# ADR 0021 — Learning REST API: thin transport, stateless-deterministic

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 4)
- **Deciders:** Architecture / CTO

## Context
The Learning Engine must be reachable over HTTP (`/learning/*`) for Decision Intelligence and the
GPT assistant. Consistent with ADR 0006/0010, business logic must not leak into the transport
layer. The engine is a four-stage pipeline (Dataset → Patterns → Statistics → Recommendations)
with no single façade object; the API has to invoke it without duplicating its logic and without
introducing shared mutable state that would make concurrent requests inconsistent.

## Decision
Add `app/api/learning.py` as a **thin transport** router mounted beside `/memory/*`. It validates
requests, **composes** the four (pure, read-only) engines into one deterministic run, and
serialises the result — it contains **no analytics of its own**. Because every stage is
deterministic, the API is **stateless**: each request **re-composes the pipeline over the current
corpus** rather than caching, so identical inputs always yield identical content and concurrent
requests are consistent without locks. Seven endpoints only: `GET summary · patterns · statistics
· recommendations · evidence/{id} · health`, `POST run`. `POST /learning/run` is **idempotent** —
a stable `run_id` per (corpus + params). Every response carries a metadata envelope
(`schema_version`, `learning_version`, `dataset_version`, `generated_at`) and the domain
**checksums** (so determinism is verifiable independently of the volatile timestamp). Filtering +
deterministic pagination on the list endpoints; error taxonomy `400/404/409/422/503`; full
OpenAPI models. It imports neither engine and writes nothing.

## Alternatives considered
- *Cache the last run in `app.state`* — rejected for now: adds shared mutable state + invalidation
  concerns; determinism makes recompute equivalent, and the live corpus is tiny. Revisit at scale.
- *Compute in the handlers* — rejected: analytics belong in the domain engines (M1–M4).
- *A `POST /run` that writes the learning tables* — deferred: through M5 the engine is read-only
  (ADR 0018); `run` reports a deterministic run without persisting. Persistence is a later concern.

## Consequences
- **Positive:** thin, auditable handlers; deterministic + thread-safe by construction; honest
  `INSUFFICIENT_DATA` on an empty corpus; complete OpenAPI; one clear route owner.
- **Negative / accepted:** recompute-per-request repeats work (bounded at current volumes);
  `generated_at` is necessarily non-deterministic (excluded from the checksums).
- **Enforced by:** per-endpoint API tests (validation, pagination, filtering, ordering, error
  taxonomy, health, evidence, concurrency, schema-version), an OpenAPI test, and a no-engine-import
  guard; the single `include_router` line is the only change outside `app/learning/`.
