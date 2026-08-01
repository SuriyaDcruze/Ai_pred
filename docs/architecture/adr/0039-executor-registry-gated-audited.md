# ADR 0039 — Executor: registry-gated invoker abstraction, immutable audit, no engine access

- **Status:** Accepted
- **Date:** 2026-08 (Sprint 7)
- **Deciders:** Architecture / CTO

## Context
Execution is where an agent could bypass every guarantee: touch an engine directly, run an unapproved
step, or lose the audit trail. It must be impossible for the Executor to run a step that is not
authorized, and it must never reach into engine internals.

## Decision
Introduce `app/agent/executor.py` (`exec-1`). `Executor.execute(plan, authorization, approvals)` runs
**only** eligible steps in plan order: a step executes iff it is `ALLOWED`, or `APPROVAL_REQUIRED` with
a **granted** `PermissionRequest`. `DENIED`, missing-approval, and dependency-blocked steps stay
**unexecuted** (`DENIED`/`SKIPPED`). Tools are invoked **only** through a replaceable `ToolInvoker`
abstraction, gated by the Tool Registry — the Executor never imports or calls an engine. The shipped
`EchoToolInvoker` is an offline deterministic stub; real engine-backed invokers are a later wiring
step. Each step yields an `ExecutionOutcome` (SUCCESS/SKIPPED/DENIED/FAILED; aggregate = worst); a tool
exception or reported failure → `TOOL_FAILURE`; an unavailable tool → `TOOL_UNAVAILABLE`. Every action
emits an immutable, auto-sequenced `AuditEntry` in execution order. Structural faults (mismatched or
incomplete authorization, unknown tool) raise `INVALID_EXECUTION`; `execute_or_raise()` additionally
raises `APPROVAL_MISSING` up front. It performs no planning and no permission evaluation.

## Alternatives considered
- *Executor calls engines directly* — rejected: couples to engine internals and risks bypassing
  read-only guarantees; the invoker abstraction keeps engines behind a replaceable seam.
- *Skip unapproved steps silently* — rejected: unapproved/denied steps are recorded (SKIPPED/DENIED)
  with an audit entry, never silently dropped.
- *Mutable audit log* — rejected: audit entries are immutable and ordered (M1 `AuditEntry`).

## Consequences
- **Positive:** authorized-only execution is structural; the audit trail is complete and ordered; the
  engine seam makes real invokers a drop-in without touching the pipeline.
- **Negative / accepted:** the shipped invoker is a stub, so execution is deterministic but not yet
  engine-backed (future work).
- **Enforced by:** 16 M5 tests (approval gating, denied/dependency skip, tool-failure, audit ordering,
  determinism) + no-engine AST.
