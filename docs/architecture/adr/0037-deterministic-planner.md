# ADR 0037 — Deterministic Planner (registry-metadata-only, no LLM in the plan path)

- **Status:** Accepted
- **Date:** 2026-08 (Sprint 7)
- **Deciders:** Architecture / CTO

## Context
Turning a task into an execution plan is the decision that most tempts non-determinism (an LLM "just
picks tools"). For an auditable, reproducible agent, the plan that reaches execution must be a pure
function of its inputs — the same task and registry must always yield the same plan.

## Decision
Introduce `app/agent/planner.py` (`plan-1`). `Planner.plan(task)` is a pure function of
`(task, registry, rules)`: it selects tools by an explicit `metadata['goal']`, an explicit
`metadata['requested_tools']` list, or a deterministic keyword scan of the description (ambiguity →
`UNSUPPORTED_TASK`); resolves each tool against the **registry metadata only** (existence →
`TOOL_NOT_FOUND`, availability → `TOOL_UNAVAILABLE`); orders steps by a **layered, cycle-detecting
topological sort** with a deterministic tie-break by tool id; and derives each `ExecutionStep`'s I/O
from the tool schema. Errors are a fixed taxonomy
(`UNSUPPORTED_TASK`/`TOOL_NOT_FOUND`/`TOOL_UNAVAILABLE`/`INVALID_PLAN`/`DEPENDENCY_ERROR`); `plan()`
captures them in a `PlanningResult`, `plan_or_raise()` raises. No LLM, no permissions, no execution,
no engine. The LLM planning adapter (ADR 0040) is advisory and must pass through this Planner.

## Alternatives considered
- *LLM-generated plans executed directly* — rejected: non-deterministic and unsafe; the LLM is
  advisory (ADR 0040) and the Planner validates.
- *Ad-hoc ordering* — rejected: a deterministic layered topological sort makes ordering reproducible
  and cycles detectable.

## Consequences
- **Positive:** identical inputs → identical plan + checksum; safe by construction (no engine, no
  execution); a single authoritative plan path that all suggestions funnel through.
- **Negative / accepted:** planning is rule-based over the current catalog (no learned selection);
  richer rules are future work.
- **Enforced by:** 16 M3 tests (selection, ordering, cycle detection, taxonomy, determinism) + AST.
