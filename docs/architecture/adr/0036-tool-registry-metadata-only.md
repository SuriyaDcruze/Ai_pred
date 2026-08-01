# ADR 0036 — Tool Registry: a metadata catalog, never an executor

- **Status:** Accepted
- **Date:** 2026-08 (Sprint 7)
- **Deciders:** Architecture / CTO

## Context
The Agent Engine (ADR 0035) needs a description of the capabilities it may plan over and execute: each
capability's id, schema, category, read/write classification, permission requirement, supported
engine, and availability. Mixing that description with execution logic would couple planning to
runtime behaviour and make the catalog untestable in isolation.

## Decision
Introduce `app/agent/tools.py` (`tool-1`) as a **metadata-only** Tool Registry. `ToolDefinition` is an
immutable, checksummed description with a canonical `<engine>.<action>` id, typed input/output
`ToolSchema`, a `ToolCategory` (with a `FUTURE_EXTENSIONS` bucket so categories are extensible), a
`ToolCapability` (`READ_ONLY`/`WRITE`), a `permission_required` flag, a `supported_engine`, a version,
and a `ToolAvailability`. Invariant: a `WRITE` tool must set `permission_required=True`. `ToolRegistry`
is an immutable functional catalog (register / duplicate-reject / lookup by id·category·engine /
deterministic order by id / discovery / round-trip). A read-only `default_registry()` describes 7
capabilities over the existing engines. The registry **executes nothing** and imports no engine.

## Alternatives considered
- *Bind a callable to each tool definition* — rejected: that is execution in the catalog; invocation
  belongs to the Executor via a replaceable invoker (ADR 0039).
- *Closed category enum with no extension path* — rejected: a `FUTURE_EXTENSIONS` bucket keeps
  categories deterministic yet open without mutable global state.
- *Free-form string tool ids* — rejected: canonical `<engine>.<action>` ids are deterministic,
  immutable, and validate cleanly.

## Consequences
- **Positive:** a deterministic, serialisable source of truth the Planner and Permission Engine read;
  fully testable without any engine; the WRITE⇒permission invariant underpins the safety floor (0038).
- **Negative / accepted:** the catalog describes capabilities but does not prove a live engine backs
  each one — real invokers are wired later (ADR 0039).
- **Enforced by:** 15 M2 tests (validation, duplicate detection, ordering, round-trip) + no-engine AST.
