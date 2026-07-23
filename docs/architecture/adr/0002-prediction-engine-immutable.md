# ADR 0002 — The Prediction Engine is immutable

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 1)
- **Deciders:** Architecture / CTO

## Context
The **Prediction Engine** (`app/ai/sklearn_model.py`, a calibrated logistic model, ~59–61%
directional) is the platform's most scrutinised, hardest-won component. Its accuracy ceiling
was established honestly across many experiments (10 feature groups, all noise), with
leakage controls, calibration, and an untouched final test. It is the ground truth every
downstream claim depends on. Any incidental change to it — even well-intentioned — risks
silently invalidating validation results and every number the product reports.

## Decision
Treat the Prediction Engine as **immutable within a sprint's feature work**. Components
built around it (Forward Testing, the API, the dashboard, future memory/learning) **consume
its outputs and import nothing from it**. Changes to the model itself happen only through a
deliberate, separately-reviewed model-training/validation process — never as a side effect
of building another feature. Automated tests assert new code does not import the engine.

## Consequences
- **Positive:** validation results stay trustworthy; new features cannot regress the model;
  clean, one-directional dependency (features → engine, never the reverse).
- **Positive:** the engine can be versioned independently (its version is stamped into every
  stored prediction), so a future swap never invalidates historical records.
- **Negative / accepted:** improvements to the model are gated behind a heavier process than
  an ordinary edit — intentional friction that protects correctness.
- **Enforced by:** import-guard tests (e.g. `test_forward_api`, `test_forward_dashboard_api`
  assert no `app.ai.sklearn_model` import) and code review.
