# ADR 0034 — Conversation REST API: thin transport, orchestration outside the routes

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 6)
- **Deciders:** Architecture / CTO

## Context
The Conversation Intelligence Engine must be reachable over HTTP. Consistent with ADR
0006/0010/0016/0021/0027, business logic must not leak into the transport layer — and the conversation
pipeline (Engine → Retrieval → Prompt → LLM) is exactly the kind of logic that could be re-implemented
in a route handler if not disciplined.

## Decision
Add `app/api/chat.py` as a **thin transport** router that **owns the `/chat/*` sub-namespace** and
**orchestrates the completed pipeline** — it classifies no intents, retrieves no Decision Intelligence
directly, builds no prompts in the routes, and calls no provider directly; every step is delegated to
the corresponding conversation module. Six endpoints: `POST /chat/message`, `POST /chat/session`,
`GET`/`DELETE /chat/session/{id}`, `GET /chat/health`, `GET /chat/version`. The engine (with its
in-memory sessions) is cached on `app.state` so sessions persist across requests. LLM error categories
map to a deterministic HTTP taxonomy (`400/404/409/429/503/500`) and provider exceptions are never
leaked. The legacy exact `POST /chat` is left untouched (ADR 0030). It writes nothing and imports
neither the Prediction nor the Outcome engine.

## Alternatives considered
- *Run the pipeline inline in the handlers* — rejected: that is business logic in transport; the
  modules (M2–M6) own it.
- *Take the exact `POST /chat`* — rejected: collides with the legacy assistant (ADR 0030).

## Consequences
- **Positive:** thin, auditable handlers; a clean HTTP surface over the whole conversation stack;
  deterministic responses; the legacy chat is undisturbed.
- **Negative / accepted:** the message endpoint is `POST /chat/message` (not the exact `POST /chat`);
  the in-memory session store is per-process (no persistence — a future concern).
- **Enforced by:** per-endpoint API tests (lifecycle, pipeline, clarification, error taxonomy incl.
  429/503, health, version, determinism, OpenAPI) + a no-engine-import guard; neighbouring API suites
  unchanged.
