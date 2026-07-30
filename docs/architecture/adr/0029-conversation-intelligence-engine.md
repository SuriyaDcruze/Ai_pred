# ADR 0029 — Conversation Intelligence Engine (read-only explanation layer)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 6)
- **Deciders:** Architecture / CTO

## Context
Sprint 5 delivered the Decision Intelligence Engine — one explainable, evidence-bound object per
prediction (`/intelligence/*`). Users still could not *ask* about it in natural language; they had
to read structured JSON. A conversational layer is valuable, but an LLM in a trading product is
dangerous: it could predict, advise, or hallucinate. So a conversation layer is only allowed if the
LLM **explains only** and every answer traces to existing deterministic outputs.

## Decision
Build a **new, separate** package `app/conversation/` — the **Conversation Intelligence Engine** — a
**read-only explanation layer** over the completed Decision Intelligence Engine. A user asks to
*explain* an existing prediction / its evidence / confidence / history / similarity / learning /
system status; the pipeline is **Conversation Engine → Intent Detection → Retrieval Orchestrator →
Prompt Builder → LLM Adapter**, and the LLM performs **explanation only**. It **never** predicts,
retrains, recalculates confidence, generates signals, gives advice, modifies data, or hallucinates;
where information does not exist it says `INSUFFICIENT_DATA` / `NOT_AVAILABLE` / `NOT_SUPPORTED`. It
talks **only** to Decision Intelligence — never the prediction models — and imports neither the
Prediction nor the Outcome engine (AST-guarded). Every module is deterministic + versioned.

## Alternatives considered
- *Let the LLM answer from raw model access* — rejected: it would predict/advise and could not be
  made deterministic or auditable.
- *Extend the legacy `app/chat/` assistant* — rejected (see ADR 0030): a pre-Decision-Intelligence
  concern; the new engine consumes DI and stays separate.

## Consequences
- **Positive:** natural-language explanations that are traceable, honest, and incapable of predicting
  or advising; a clean 7-module pipeline, each independently testable.
- **Negative / accepted:** on a thin corpus most answers report `INSUFFICIENT_DATA` — the honest
  young-system behaviour.
- **Enforced by:** AST import-guards; per-module determinism + honesty tests; unchanged Sprint 1–5
  suites each milestone.
