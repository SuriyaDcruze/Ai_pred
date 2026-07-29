# ADR 0017 — Behavioural Learning Engine, separate from the meta-model retrainer

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 4)
- **Deciders:** Architecture / CTO

## Context
Volume 15 ("Learning Engine") already names an existing subsystem: the **meta-model retrainer**
(`app/training/meta.py`, `nightly_retrain.py`, `walk_forward.py`) that *trains* a meta-model and
promotes a challenger through the validated champion/challenger pipeline (ADR 0002/0003). Sprint 4
asks for a **different** thing under the same name: a layer that reads completed Historical Memory
and surfaces statistically honest, descriptive observations — *no training, no models, no
prediction*. Conflating the two would put a retrainer's expectations (it mutates model artifacts)
onto a read-only analytics layer, and vice-versa.

## Decision
Build the Sprint 4 capability as a **new, separate subsystem** in its own package `app/learning/`
(peer of `app/memory/`, `app/similarity/`), and call it the **Behavioural Learning / Learning
Analytics Engine** in the docs. It performs **descriptive statistical analytics only** over
completed decisions and **never** trains, promotes, or invokes a model. The legacy meta-model
retrainer stays exactly where it is, unchanged, a separate future concern under the model process.
Volume 15 carries an explicit disambiguation block naming both senses.

## Alternatives considered
- *Extend `app/training/` with the analytics* — rejected: fuses a mutating trainer with a
  read-only analyser; muddies the "no model change" guarantee and the ADR 0002/0003 boundary.
- *Rename the retrainer to free the name* — rejected: needless churn to a built, tested subsystem.
- *Call the new package `app/analytics/`* — rejected: the mandate's word is "Learning"; the
  package is `app/learning/` with the docs disambiguating the two senses.

## Consequences
- **Positive:** the two subsystems have disjoint responsibilities and guarantees; the read-only
  analyser can never accidentally change a model; each evolves independently.
- **Negative / accepted:** "Learning Engine" is overloaded — mitigated by the Volume 15
  disambiguation and this ADR.
- **Enforced by:** package separation; AST import-guard tests (`app/learning/*` imports neither
  the Prediction nor the Outcome engine); no-write tests; the Volume 15 disambiguation section.
