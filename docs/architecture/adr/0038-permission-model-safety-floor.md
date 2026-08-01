# ADR 0038 — Permission model: a metadata safety floor, policy tightens-only

- **Status:** Accepted
- **Date:** 2026-08 (Sprint 7)
- **Deciders:** Architecture / CTO

## Context
The Agent Engine's core safety promise is that no tool runs without approval and a state-changing tool
can **never** be silently allowed. A purely policy-driven model is dangerous: a permissive (or buggy)
policy rule could authorize a write tool with no approval, bypassing the guarantee.

## Decision
Introduce `app/agent/permissions.py` (`perm-1`). Authorization of each plan step combines two layers:
1. a **metadata safety floor** derived from the tool — a `WRITE` or `permission_required` tool floors
   at `APPROVAL_REQUIRED`, everything else at `ALLOWED`;
2. a **policy** of ordered first-match `PermissionRule`s (match by tool_id / category / capability) with
   a `default_level` fallback.

The effective level is the **strictest** of (policy_level, floor) — so policy can only *tighten*, never
loosen: it can `DENY` anything or require approval for an otherwise-allowed tool, but it can never
relax a state-changing tool below `APPROVAL_REQUIRED`. Each step gets a `StepAuthorization`; the
aggregate `AuthorizationResult.overall` is the strictest step level. Every `APPROVAL_REQUIRED` step
yields a deterministic `PermissionRequest` left **PENDING** — the engine **never auto-approves**.
Errors: `POLICY_ERROR`/`INVALID_PERMISSION`/`APPROVAL_REQUIRED`/`PERMISSION_DENIED`. It reads tool
metadata only; it executes nothing and persists nothing.

## Alternatives considered
- *Policy is the sole authority* — rejected: a permissive rule could bypass the write guarantee; the
  floor makes the guarantee structural, not policy-dependent.
- *Auto-approve read-only-with-permission tools* — rejected: if the metadata says a tool needs
  approval, the engine honours it; only the caller grants approvals.

## Consequences
- **Positive:** the read-only-by-default guarantee is enforced in code regardless of policy; policies
  stay simple and can only add restriction; approvals are explicit and auditable.
- **Negative / accepted:** a policy cannot "trust" a write tool into running without approval (by
  design).
- **Enforced by:** 14 M4 tests (floor cannot be loosened, tighten-to-denied, approval generation,
  taxonomy, determinism) + no-engine AST.
