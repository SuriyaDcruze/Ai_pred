# ADR 0027 — Decision Intelligence REST API: thin transport, single route owner

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 5)
- **Deciders:** Architecture / CTO

## Context
The Decision Intelligence Engine must be reachable over HTTP for the dashboard and the future GPT
assistant. Consistent with ADR 0006/0010/0016/0021, business logic must not leak into the transport
layer. Two hazards: (a) the API could re-implement composition/explanation/confidence; (b) the
`/intelligence` namespace already has a **legacy exact-path route** (`GET /intelligence`, the V3
live-analysis view) — adding sub-routes risks a collision or a route-ordering trap.

## Decision
Add `app/api/intelligence.py` as a **thin, deterministic, read-only** router that **owns the
`/intelligence/*` sub-namespace**. It validates a request, invokes the existing pipeline —
**compose** (M2) → **explain** (M3) → **assess** (M4) — and serialises the result; it contains **no
business logic**. Four endpoints only: `GET /intelligence/{prediction_id}`, `/symbol/{symbol}`,
`/health`, `/version`. Static routes (`/health`, `/version`, `/symbol/{symbol}`) are declared
**before** the `/{prediction_id}` catch-all (ADR 0016 discipline); the legacy exact `GET
/intelligence` is left untouched (no duplicate). **Deterministic serialisation** — stable content +
domain checksums only, **no wall-clock timestamp** — so identical objects yield byte-identical
responses. Error taxonomy `400/404/409/422/503`; explicit API / DI / schema versions; full OpenAPI.
It writes nothing, runs no model/search, and imports neither the Prediction nor the Outcome engine.

## Alternatives considered
- *Compute in the handlers* — rejected: composition/explanation/confidence belong in the domain
  engines (M2–M4).
- *A separate `/decision-intelligence/*` prefix* — rejected: the namespace is `/intelligence`; a
  single owner + ordered routes resolves the collision cleanly.
- *Include a `generated_at` wall-clock in the response* — rejected: it would make identical objects
  serialise differently; determinism is proved by the checksums instead.

## Consequences
- **Positive:** thin, auditable handlers; deterministic + thread-safe by construction; one clear
  route owner; complete OpenAPI; the legacy route is undisturbed.
- **Negative / accepted:** recompute-per-request repeats the pipeline (bounded at current volumes;
  a cache is a future option). The single `include_router` line is the only change outside the DI
  package.
- **Enforced by:** per-endpoint API tests (validation, determinism, error taxonomy, health, version,
  catch-all ordering, OpenAPI), a no-engine-import guard, and the unchanged neighbouring API suites.
