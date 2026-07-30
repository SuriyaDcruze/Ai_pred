# ADR 0025 — Evidence-based, descriptive explanation (never advice)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 5)
- **Deciders:** Architecture / CTO

## Context
A composed decision object is only useful if a human (or the future GPT layer) can see **where every
piece of information came from and what supports it**. But an "explanation" layer is dangerous: it
could drift into persuasion or prescription ("do X"), which would manufacture the prescriptive edge
claim the project has repeatedly disproven and collide with the SEBI decision-support posture
(Vol 03/24).

## Decision
The Evidence & Explanation Engine (M3) turns a composed object into a **deterministic evidence
graph** (root → subsystem → facet), a **provenance map** (every node → its source), a **For/Against**
breakdown, a **missing-evidence** list (`INSUFFICIENT_DATA` / `NOT_AVAILABLE` / `NOT_SUPPORTED`), and
a **descriptive** explanation with a standing disclaimer (*"not a prediction, recommendation, or
advice"*). It **re-labels already-composed verbatim data into a graph** — it computes no new
prediction/statistic/recommendation. Rules are honesty-bound: **no explanation without evidence, no
evidence without provenance** (orphans/duplicates are rejected); For/Against conflict signals are
**factual, stored-figure** observations (e.g. the outcome model's own veto probability), never
opinions. Language is descriptive, never persuasive/predictive/advisory (asserted by a no-advice
test).

## Alternatives considered
- *A free-text / LLM-generated narrative* — rejected here: non-deterministic and unauditable; the
  GPT layer (Vol 07, future) may *phrase* this structure, but the structure itself must be
  deterministic and evidence-bound.
- *Fabricate a "best guess" where data is missing* — rejected: missing evidence is reported with a
  reason, never invented.
- *Omit limitations/disclaimers when the picture looks strong* — rejected: always present.

## Consequences
- **Positive:** every field is explainable and auditable to its origin; deterministic + serialisable
  (byte-identical for identical inputs); SEBI-aligned (descriptive decision-support, not advice).
- **Negative / accepted:** the language is deliberately hedged and never actionable-sounding.
- **Enforced by:** determinism + serialization tests; provenance/orphan/duplicate validation;
  missing-evidence + no-advice tests; read-only-over-the-object (source unchanged).
