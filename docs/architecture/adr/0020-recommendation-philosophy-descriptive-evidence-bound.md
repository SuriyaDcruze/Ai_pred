# ADR 0020 — Recommendation philosophy: descriptive, evidence-bound, never advice

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 4)
- **Deciders:** Architecture / CTO

## Context
The word "recommendation" is dangerous here. A layer that emits *"do X"* would (a) manufacture the
prescriptive edge claim the project has repeatedly disproven and (b) collide with the SEBI
decision-support posture (Aegis is education/decision-support, not registered advice — Vol 03/24).
The Recommendation Engine (Milestone 4) must package validated statistics for humans and the GPT
assistant to *explain*, without becoming a trade signal.

## Decision
Recommendations are **descriptive, evidence-bound observations** — never advice, never predictions:
- **Only `VALIDATED` patterns** become recommendations; none → `INSUFFICIENT_DATA`.
- **Descriptive framing is enforced:** *"Historically, … resolved with an X% win rate across N
  trades (95% CI …)"*, tagged as a **hypothesis to validate in Forward Testing**. A test asserts
  advice phrases ("you should", "will win", "take this trade", …) never appear.
- **Evidence is mandatory and auditable:** every recommendation carries the supporting
  `prediction_id`s, the verbatim statistical basis, the pattern identity, and an **always-non-empty
  `limitations`** list (sample size, regime/timeframe dependence, CI width, instability).
- **Communication confidence ≠ statistical significance.** `recommendation_confidence`
  (`HIGH`/`MEDIUM`/`LOW`) measures confidence in *communicating* the observation, from a
  deterministic rubric over sample size, CI width, consistency, and evidence traceability — not
  the p-value. A significant-but-thin pattern is still `LOW`.
- Evidence ids are sourced from the Milestone 2 candidate patterns, so the Milestone 3 output is
  left untouched (ADR 0018).

## Alternatives considered
- *Emit prescriptive "take/avoid" actions with an `expected_benefit`* — rejected: prescriptive
  advice, SEBI-incompatible, and an unproven edge claim.
- *Reuse statistical significance as the recommendation's confidence* — rejected: significance
  answers "is it real?"; it says nothing about whether the observation is *communicable* to a
  human; conflating them overstates thin results.
- *Omit limitations when a pattern looks strong* — rejected: limitations are always mandatory.

## Consequences
- **Positive:** outputs are honest, auditable, SEBI-aligned observations that the GPT layer can
  explain without ever giving advice; every claim traces to originating trades.
- **Negative / accepted:** the language is deliberately hedged and never actionable-sounding;
  users wanting "just tell me what to do" get an observation + a hypothesis, by design.
- **Enforced by:** the no-advice language test; evidence-traceability + non-empty-limitations
  tests; a confidence-independent-of-significance test; determinism + identity tests.
