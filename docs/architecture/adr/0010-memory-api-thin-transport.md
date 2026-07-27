# ADR 0010 — The `/memory/*` API is thin transport only

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 2)
- **Deciders:** Architecture / CTO

## Context
The Historical Memory REST API exposes retrieval + build operations over HTTP. As with the
`/forward/*` API (ADR 0006), the risk is business logic leaking into handlers — composing
records, computing aggregates, or querying the database directly — which entangles transport
with the domain and invites duplication.

## Decision
`/memory/*` handlers (`app/api/memory.py`) are a **thin transport layer** (thin-controller
pattern): validate input (FastAPI `Query` bounds + pydantic), call the `RetrievalEngine` /
`MemoryBuilder`, and shape JSON — nothing more. **No business logic, no direct DB access, no
engine imports.** Domain errors map to consistent HTTP codes — 404 (unknown), 422 (invalid
filter/cursor/dimension), 500 (generic) — and **never leak a stack trace**. The router is
mounted in `app/api/main.py` beside `/forward/*`; the domain objects are created once in the
app lifespan and shared via `app.state`.

## Consequences
- **Positive:** the domain is testable without HTTP; the API surface stays small and
  auditable; the presentation layer can be swapped without touching domain logic; the same
  endpoints can serve the GPT assistant as read-only tools.
- **Positive:** aggregation and composition live in exactly one place (the engines), never
  re-implemented in a handler or a browser.
- **Negative / accepted:** a genuinely new view can require a small additive endpoint rather
  than being assembled client-side — the correct trade to keep logic server-side.
- **Enforced by:** AST import-guard tests (no `app.ai.*`) and the rule that handlers touch
  only the retrieval/builder objects.
