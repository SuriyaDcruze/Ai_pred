# ADR 0023 — Decision Intelligence Engine (composition/serving layer)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 5)
- **Deciders:** Architecture / CTO

## Context
Sprints 1–4 built four **independent** read surfaces — Forward Testing (`predictions`), Historical
Memory, the Similarity Engine, and the Behavioural Learning Engine — each answering one question.
Nothing yet **composes** them into a single, explainable answer to *"what does the whole system
think about this decision, and why?"* A caller had to hit `/forward/*`, `/memory/*`,
`/memory/similar*`, and `/learning/*` separately and stitch the story together. Separately, the word
"intelligence" was already used by a **legacy, pre-Memory** layer (`app/intelligence.py` V3,
`app/sector.py`, Vol 08) that computes a *fresh* per-symbol analysis from live market data + the
models directly — a different thing.

## Decision
Build the Sprint 5 capability as a **new, separate subsystem** in its own package
`app/decision_intelligence/` (peer of `app/memory/`, `app/similarity/`, `app/learning/`), the
**Decision Intelligence Engine** — the composition + serving node *after* the four engines. It
**composes** their already-produced outputs (it does not re-run market analysis), adds a
traceability/evidence graph and a descriptive explanation, gates everything on honesty, and serves
the result at `/intelligence/*`. The legacy `app/intelligence.py` stays a separate live-analysis
concern (a later sprint may supersede it — never a destructive edit); the two are distinguished in
the docs.

## Alternatives considered
- *Extend the legacy `app/intelligence.py`* — rejected: it computes a fresh analysis from market
  data + models; Sprint 5 composes stored engine outputs over the *historical* picture. Fusing them
  would blur "reads models live" with "read-only over stored data".
- *A `/decision/*` prefix / merge into an existing engine* — rejected: the mandate's word is
  "Decision Intelligence"; a peer package with a single API owner keeps boundaries clean.

## Consequences
- **Positive:** one explainable object instead of four stitched calls; a clean seam between the four
  engines and the explanation/serving layer (it depends on all, is depended on by none — no cycles).
- **Negative / accepted:** "intelligence" is overloaded — mitigated by the disambiguation + this ADR.
- **Enforced by:** package separation; AST import-guards (`app/decision_intelligence/*` +
  `app/api/intelligence.py` import neither the Prediction nor the Outcome engine); the unchanged
  Sprint 1–4 suites re-run green each milestone.
