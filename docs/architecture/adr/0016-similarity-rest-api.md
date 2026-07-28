# ADR 0016 — Similarity REST API (thin transport, single route owner)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 3)
- **Deciders:** Architecture / CTO

## Context
The Similarity Engine must be reachable over HTTP. The `/memory/similar/{id}` placeholder route
already lived in Sprint 2's `app/api/memory.py`. Adding sibling routes (`/health`, `/search`, a
collection form) risks a **path collision** and a **route-ordering trap**: `/memory/similar/
health` would be swallowed by `/memory/similar/{prediction_id}` if the catch-all is registered
first.

## Decision
Create `app/api/similarity.py` as a **thin transport** router (thin-controller pattern, ADR
0006/0010) that **owns every `/memory/similar*` route** — moving the placeholder out of
`memory.py`. Within the router, the static paths (`/health`, `/search`, the collection `""`)
are declared **before** the `/{prediction_id}` catch-all so they resolve correctly. The
`SimilaritySearchEngine` is created in the app lifespan and **injected into `RetrievalEngine`**
(ADR 0015), so the endpoints are live. No search algorithm lives in the API. Errors map to a
consistent taxonomy: `400` validation, `404` prediction/embedding, `409` version mismatch,
`503` engine unavailable. Responses **never** expose raw embeddings or feature vectors.

## Alternatives considered
- *Keep the route in `memory.py` and enhance it in place* — rejected: split ownership of
  `/memory/similar*` across two routers, and the `/health` ordering trap remained.
- *A separate `/similarity/*` prefix* — rejected: the spec and contract are `/memory/similar`.
- *Compute/serialise in the handler* — rejected: business logic belongs in the domain (M3/M4).

## Consequences
- **Positive:** one clear owner; correct route ordering; thin, auditable handlers; live
  endpoints; honest response shape (sample size always present).
- **Negative / accepted:** moving the route touched a Sprint 2 API file (`memory.py`) and moved
  its two similarity tests — an intentional, flagged productionisation; other `/memory/*`
  behaviour is unchanged. FastAPI type-coercion still yields `422` (project standard) alongside
  the `400` business-validation errors.
- **Enforced by:** API tests for every endpoint, the error taxonomy, OpenAPI generation, and a
  no-engine-import guard.
