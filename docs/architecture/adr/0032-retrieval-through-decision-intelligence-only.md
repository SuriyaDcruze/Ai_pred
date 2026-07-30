# ADR 0032 — Retrieval through Decision Intelligence only (transport-independent)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 6)
- **Deciders:** Architecture / CTO

## Context
To answer a question the conversation layer needs data — the composed decision, its evidence,
confidence, history, similar cases, learning. It could reach into Historical Memory, Similarity, and
Learning directly, but that would (a) bypass the Decision Intelligence Engine that already composes
and gates those honestly, and (b) couple the conversation layer to four engines and their transports.

## Decision
The **Retrieval Orchestrator** (`app/conversation/retrieval.py`) retrieves **only through the Decision
Intelligence Engine** — never the Prediction/Memory/Similarity/Learning engines directly. It reaches
Decision Intelligence through a **transport-independent** `DecisionIntelligenceSource` adapter, so an
in-process, REST, or RPC transport is interchangeable **without changing orchestration logic**
(`InProcessSource` is the concrete adapter, going through the DI engine's own compose→explain→assess).
It performs **retrieval only** (no generation, prompts, or LLM), runs a deterministic pipeline
(validate → select targets → fetch once → slice → merge), preserves each component's availability +
provenance + citations verbatim, and the orchestrator core imports **no engine at all**.

## Alternatives considered
- *Query Memory/Similarity/Learning directly* — rejected: bypasses DI's honesty gates + composition,
  duplicates wiring, and couples to four engines.
- *Bake the REST transport into the orchestrator* — rejected: the source-adapter seam keeps the core
  transport-independent and testable with fakes.

## Consequences
- **Positive:** one source of truth (DI); the orchestrator is deterministic, transport-swappable, and
  honesty-preserving (`INSUFFICIENT_DATA`/`NOT_AVAILABLE`/`NOT_SUPPORTED`/`ERROR` flow straight
  through).
- **Negative / accepted:** an extra adapter indirection; the in-process source recomposes per request
  (bounded at current volumes).
- **Enforced by:** orchestrator tests (routing, availability, merge, determinism) + a no-engine-import
  guard on the core; a real in-process DI integration test.
