# ADR 0001 — Modular monolith (not microservices)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 1)
- **Deciders:** Architecture / CTO

## Context
Aegis is a single-team product at an early, evidence-gathering stage. Its core value is a
pair of ML models and the honest machinery around them, not distributed-systems scale. We
must ship a Forward Testing Engine and iterate quickly, with strong internal boundaries but
minimal operational overhead. Microservices would add network hops, deployment surface,
distributed-failure modes, and cross-service data consistency problems we do not need.

## Decision
Build Aegis as a **modular monolith**: one deployable FastAPI application, internally
partitioned into packages with clear ownership and one-directional dependencies
(`app/ai`, `app/forward_testing`, `app/api`, `app/database`, `app/features`, …). New
capabilities are new **packages/modules**, not new services. No message bus, no service
mesh, no per-feature datastore. In-process asyncio tasks cover background work (e.g. the
Forward Testing monitor) instead of external workers.

## Consequences
- **Positive:** one process to run and reason about; fast local development; simple,
  atomic refactors across boundaries; no network/serialization tax between components;
  transactions stay local to one database.
- **Positive:** boundaries are still enforced (package structure, import discipline,
  tests that assert e.g. the API imports nothing from the engines).
- **Negative / accepted:** no independent per-service scaling or deployment; a runaway
  component shares the process. Acceptable at current scale.
- **Revisit when:** sustained load or team growth makes independent scaling/deploys worth
  the complexity — extract the hottest module behind its existing interface (a new ADR).
