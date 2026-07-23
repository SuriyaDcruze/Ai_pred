# ADR 0006 — REST API separated from engine logic

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 1)
- **Deciders:** Architecture / CTO

## Context
The `/forward/*` endpoints must expose Forward Testing over HTTP. A common anti-pattern is
to let HTTP handlers grow business logic — running models, computing domain aggregates,
querying the database directly — which entangles the transport layer with the domain, makes
logic untestable without HTTP, and invites duplication (e.g. the dashboard re-implementing
aggregation in the browser).

## Decision
Keep the **API a thin transport layer** over the domain. Handlers (`app/api/forward.py`, an
`APIRouter`) validate input (pydantic `ForwardPredictionRequest`), call the
`PredictionStore` / `ForwardTestingEngine`, and shape JSON — nothing more. **No model logic
in the API** and **no engine imports** (asserted by tests). Where the dashboard needed
aggregates the base store did not expose (grouped breakdowns, live-vs-backtest), that logic
lives **server-side** in a dedicated, unit-testable module (`app/api/forward_analytics.py`,
pure functions) — never in the browser and never as a direct DB call from the frontend. The
dashboard is presentation-only.

## Consequences
- **Positive:** domain logic is testable without HTTP; the API surface stays small and
  auditable; one server-side place owns each computation (no browser/duplicate math).
- **Positive:** the presentation layer can be replaced (new dashboard, mobile) without
  touching domain logic; the API can be reused by the LLM assistant as read-only tools.
- **Negative / accepted:** serving a genuinely new view can require a small additive API
  extension (as M5 added `/forward/breakdown`) rather than being done purely in the client —
  the correct trade to keep logic server-side.
- **Enforced by:** AST import-guard tests and the rule that handlers only touch the store
  and pure helpers.
