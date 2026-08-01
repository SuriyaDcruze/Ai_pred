# ADR 0041 — Agent REST API: thin transport, single route owner (`/agent/*`)

- **Status:** Accepted
- **Date:** 2026-08 (Sprint 7)
- **Deciders:** Architecture / CTO

## Context
The Agent Engine must be reachable over HTTP. Consistent with ADR 0006/0010/0016/0021/0027/0034,
business logic must not leak into the transport layer — and the agent pipeline (plan → authorize →
execute, plus advisory suggest) is exactly the kind of logic that could be re-implemented in a route
handler if not disciplined.

## Decision
Add `app/api/agent.py` as a **thin transport** router that **owns the `/agent/*` namespace**
(`agent-api-1`) and **delegates every step** to the components: it performs no planning, authorization,
execution, or provider logic. Nine endpoints: `POST /agent/session`, `GET`/`DELETE
/agent/session/{id}`, `POST /agent/plan`, `POST /agent/authorize`, `POST /agent/execute`,
`POST /agent/suggest`, `GET /agent/health`, `GET /agent/version`. The pipeline is **artifact-passing**
— `/plan` returns an `AgentPlan`, `/authorize` consumes it and returns an `AuthorizationResult`,
`/execute` consumes both (+ granted request ids) — so each route is a pure transformation. Every
component fault maps to a deterministic HTTP status (planner `404/409/422`, permission `400/422`,
execution `422/409/502`, malformed payload `400`; provider faults normalised **in-body**) and no
internal exception leaks. The components are cached on `app.state`; agent sessions are **in-memory**
(no persistence). Mounted with one additive `include_router` line in `app/api/main.py`. It imports no
engine.

## Alternatives considered
- *Run the pipeline inline in the handlers* — rejected: that is business logic in transport; the
  components (M2–M6) own it.
- *Persist sessions in a database* — rejected for this sprint: in-memory keeps the transport free of a
  new storage concern (ADR 0028/0034 precedent); persistence is future work.
- *One mega-endpoint `POST /agent/run`* — rejected: the artifact-passing pipeline keeps each stage
  independently testable and observable, and lets a caller insert approvals between authorize and
  execute.

## Consequences
- **Positive:** thin, auditable handlers; a clean HTTP surface over the whole agent stack; deterministic
  responses; approvals can be granted between authorize and execute.
- **Negative / accepted:** in-memory sessions are per-process; clients pass plan/authorization artifacts
  forward between calls.
- **Enforced by:** 15 M7 API tests (routing, pipeline, session lifecycle, error normalization, health,
  version, OpenAPI) + a no-engine-import guard; neighbouring API suites unchanged.
