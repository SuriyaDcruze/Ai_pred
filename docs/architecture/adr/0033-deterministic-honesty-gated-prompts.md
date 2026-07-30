# ADR 0033 — Deterministic, honesty-gated prompt construction

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 6)
- **Deciders:** Architecture / CTO

## Context
The prompt is where an LLM could be pushed to predict, advise, or invent. If prompt construction were
non-deterministic or allowed to add facts, the whole "explanation-only, traceable" guarantee would
collapse. The prompt must faithfully represent retrieved Decision Intelligence and constrain the LLM
to explanation.

## Decision
The **Prompt Builder** (`app/conversation/prompt.py`) is **deterministic** and **never invents,
reorders incorrectly, or modifies retrieved content**. Given identical retrieval it produces an
identical prompt (SHA-256 checksum). It assembles a **fixed section order** (System → Conversation
Context → Retrieved Decision Intelligence → Evidence → Composite Confidence → Historical → Similar →
Learning → Citations → User Request); a **system prompt** that enforces *explanation only, no
prediction, no advice, no hallucination, use retrieved evidence only, preserve citations, report
unavailable honestly*; per-intent instruction templates; verbatim context (availability preserved);
deterministic citation formatting with **missing-citation rejection**; and a **deterministic token
budget** that trims lowest-priority context first while **always** preserving the system
instructions, the user request, and the citations. It imports no engine or LLM.

## Alternatives considered
- *Free-form / LLM-assembled prompt* — rejected: non-deterministic, unauditable, and could inject
  facts.
- *Drop citations under token pressure* — rejected: citations are never trimmed; missing citations
  are rejected outright.

## Consequences
- **Positive:** a reproducible, auditable prompt that constrains the LLM to honest explanation; every
  cited statement traces to a source.
- **Negative / accepted:** verbose retrieved JSON can be trimmed under tight budgets (trimming is
  deterministic and recorded); the language is deliberately hedged.
- **Enforced by:** prompt tests (fixed order, verbatim content, citations + missing-citation, token
  budget + deterministic truncation, no-advice system prompt) + a no-engine/LLM-import guard.
