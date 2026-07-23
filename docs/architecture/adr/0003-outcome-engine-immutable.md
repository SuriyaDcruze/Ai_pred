# ADR 0003 — The Outcome Engine is immutable

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 1)
- **Deciders:** Architecture / CTO

## Context
The **Outcome Engine** (`app/ai/outcome_model.py`, a HistGradientBoosting meta-labeller
answering *"will this trade hit its target before its stop?"*) is the project's **first and
only verified edge**. It survived an untouched final test (PF 2.31) and generalised across
crypto and Indian NSE stocks (`reports/outcome_model_summary.md`). It is meta-labeling in
the sense of López de Prado — its correctness depends on careful leakage control and
non-overlapping sampling. This is the IP Forward Testing exists to validate live; any
accidental change would compromise exactly what we are trying to prove.

## Decision
Treat the Outcome Engine as **immutable within feature work**, on the same terms as the
Prediction Engine (ADR 0002). Forward Testing and everything downstream **consume its
outputs and import nothing from it**. Its version is stamped independently into every stored
prediction (`outcome_model_version`). Retraining/replacement is a separate, deliberately
reviewed process, never a side effect of building other features.

## Consequences
- **Positive:** the one proven edge cannot be silently degraded; the live forward test
  measures the *same* model that was validated in backtest.
- **Positive:** independent versioning means a future model swap coexists with historical
  records rather than invalidating them.
- **Negative / accepted:** the same intentional friction on model changes as ADR 0002.
- **Enforced by:** import-guard tests and code review; Forward Testing imports only the
  Prediction **Store**, never either engine.
