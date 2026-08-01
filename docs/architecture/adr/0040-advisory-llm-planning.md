# ADR 0040 — Advisory LLM planning: the deterministic Planner is the authority

- **Status:** Accepted
- **Date:** 2026-08 (Sprint 7)
- **Deciders:** Architecture / CTO

## Context
An LLM can help propose a tool sequence for a task, but LLM output is non-deterministic and untrusted.
The Agent Engine's plans must stay reproducible and safe (ADR 0037), so an LLM must never produce a
plan that reaches execution without deterministic validation.

## Decision
Introduce `app/agent/planning_llm.py` (`planllm-1`) as an **advisory-only**, provider-independent
adapter (mirroring the Sprint 6 LLM adapter, ADR 0031). `PlanningLLMAdapter.suggest(request)` returns
a structured `PlanningResponse` (a `SuggestedStep` sequence + rationale + provider-only confidence +
notes) — explicitly **not** an executable plan. The adapter validates the suggestion against the
request (schema, tool-id validity against the available tools, duplicates, unsupported tools,
malformed dependencies) and normalises any failure to `INVALID_RESPONSE`; provider faults normalise to
`INVALID_REQUEST`/`INVALID_RESPONSE`/`PROVIDER_UNAVAILABLE`/`RATE_LIMITED`/`TIMEOUT`/`INTERNAL_ERROR`
and are never leaked. Providers are duck-typed (`EchoPlanningProvider` offline stub +
`OpenAIPlanningProvider`/`azure_openai`) with **no SDK import**. `as_requested_tools()` yields
`(tool_ids, dependencies)` a caller feeds to the **deterministic Planner (ADR 0037)**, which remains
the sole authority for the plan that reaches the Permission Engine and Executor. The module imports no
engine and **not the Planner** (AST) — it only produces suggestions the Planner independently
validates.

## Alternatives considered
- *Let the adapter emit an `AgentPlan` directly* — rejected: that bypasses the deterministic Planner
  and its safety/validation guarantees.
- *Import an LLM SDK* — rejected: keeps the adapter pure infrastructure and dependency-free (ADR 0031
  precedent); providers take a thin duck-typed client.

## Consequences
- **Positive:** LLM assistance without sacrificing determinism or safety; every suggestion funnels
  through the Planner; providers are swappable; latency excluded from checksums.
- **Negative / accepted:** the shipped provider is an offline stub; a live provider needs a translator
  + credentials.
- **Enforced by:** 11 M6 tests (advisory-then-Planner-authority, validation, normalization,
  determinism) + a no-engine/no-Planner AST guard.
