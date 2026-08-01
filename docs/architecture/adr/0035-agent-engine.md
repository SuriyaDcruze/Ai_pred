# ADR 0035 — Agent Engine (deterministic planning + permissioned tool-execution layer)

- **Status:** Accepted
- **Date:** 2026-08 (Sprint 7)
- **Deciders:** Architecture / CTO

## Context
AEGIS exposes six read-only engines (`/forward/*`, `/memory/*`, `/memory/similar*`, `/learning/*`,
`/intelligence/*`, `/chat/*`). The mandate calls for an orchestration layer that can take a task, plan
a sequence of capability calls over those engines, obtain explicit approval for anything that could
change state, execute the approved steps, and keep an audit trail — **without** ever predicting,
advising, or bypassing the honesty/read-only guarantees the engines already enforce (ADR 0002/0003/
0018/0024/0025).

## Decision
Add a new package `app/agent/` — the **Agent Engine** — as a deterministic, layered pipeline:
domain model (`agt-1`) → tool registry (`tool-1`) → planner (`plan-1`) → permission engine (`perm-1`)
→ executor (`exec-1`) → advisory LLM planning adapter (`planllm-1`), served at `/agent/*`
(`agent-api-1`). It is **read-only by default**: tools are a metadata catalog, the executor calls
tools only through a replaceable invoker abstraction (the shipped one is an offline stub), and any
state-changing tool requires an explicit `PermissionRequest`. Every layer is frozen, deterministic
(sha1 ids + SHA-256 checksums, volatile timestamps excluded), serialisable, and imports **neither**
the Prediction nor the Outcome engine (AST-guarded). It is a peer of `app/conversation/` and
`app/decision_intelligence/` and is distinct from the legacy `app/chat/`.

## Alternatives considered
- *Fold agent behaviour into the Conversation Engine* — rejected: explaining (Sprint 6) and
  planning/executing-under-permission are different responsibilities with different risk profiles.
- *Let the LLM produce and run plans directly* — rejected: non-deterministic and unsafe; the LLM is
  advisory only and the deterministic Planner is the authority (ADR 0040).
- *Have the agent call engines directly* — rejected: couples the agent to engine internals and risks
  bypassing read-only guarantees; tools are metadata and execution goes through an invoker (ADR 0039).

## Consequences
- **Positive:** a clean, testable orchestration layer with an explicit approval gate and audit trail;
  every prior engine and guarantee is untouched; deterministic and reproducible end-to-end.
- **Negative / accepted:** with only offline stub invokers, execution does not yet call real engines
  (future work); agent sessions are in-memory (no persistence).
- **Enforced by:** 104 Sprint-7 tests (per-module) + AST no-engine guards; Sprint 1–6 suites unchanged.
