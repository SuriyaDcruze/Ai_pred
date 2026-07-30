# ADR 0030 — Conversation Intelligence coexists with the legacy chat assistant

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 6)
- **Deciders:** Architecture / CTO

## Context
A chat layer already existed before Decision Intelligence: `app/chat/` (`TradingAssistant`,
`LLMAssistant`) — a rule-based + optional-LLM assistant wired at `app.state.assistant` and exposed at
the exact route `POST /chat`. Sprint 6 introduces a *different* conversational layer that consumes the
completed Decision Intelligence Engine. Two things named "chat" must not collide or be conflated.

## Decision
The Sprint 6 engine is a **new, separate** package `app/conversation/` (the Conversation Intelligence
Engine), distinct from the legacy `app/chat/`. The legacy assistant **stays in place, unchanged** —
superseding it is explicitly out of scope. The new REST layer owns the **`/chat/*` sub-namespace**
and exposes its message endpoint as **`POST /chat/message`**, leaving the legacy exact **`POST /chat`**
untouched (single-route-owner discipline, ADR 0016). A later sprint may supersede the legacy chat —
never via a destructive edit here.

## Alternatives considered
- *Replace the legacy `POST /chat`* — rejected: supersession is out of scope; other consumers may
  still use it; a destructive edit breaks the "prior work unchanged" guarantee.
- *A fully distinct prefix (`/conversation/*`)* — considered; the spec + filename (`app/api/chat.py`)
  point to `/chat`, so the sub-namespace `/chat/*` with `POST /chat/message` was chosen instead.

## Consequences
- **Positive:** zero collision; the legacy chat and the new engine coexist; the new namespace is
  clearly the Conversation Intelligence owner.
- **Negative / accepted:** the message endpoint is `POST /chat/message`, not the exact `POST /chat`
  (a small deviation from the "ideal" path, forced by the legacy route).
- **Enforced by:** the router owning `/chat/*`; API tests + OpenAPI; the legacy `POST /chat` tests
  unchanged and green.
