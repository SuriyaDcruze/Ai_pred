# Volume 26 — Testing Strategy

## Purpose
Define how Aegis stays correct — and, uniquely, how testing enforces **research integrity**
(no leakage, honest evaluation), not just code correctness.

## Status: 🟢 Strong — **361 tests** (full suite passing), incl. leakage/future-invariance, the honest pipeline, and the Sprint 1 Forward Testing suite (128 tests).

## The test pyramid (Aegis-specific)
```
        e2e / smoke        endpoint smoke, JS syntax, dashboard flows
      integration          service ↔ engines, screener, intelligence
   unit                    models, features, risk, rules, sector, outcome
 RESEARCH-INTEGRITY tests  ← the layer most projects lack
```

## Research-integrity tests (the differentiator)
- **Future-invariance / leakage** (`tests/test_feature_leakage.py`): corrupt all candles
  after a cut-point, assert no earlier feature value changes — for every feature group.
  Plus an AST guard banning `shift(-1)` / centered rolling in feature code.
- **Walk-forward + accept/reject gates** (`tests/test_walk_forward.py`): the harness
  scores real signal, is ~chance on noise, and the promotion gate **rejects noise &
  class-imbalance**, accepts only a real balanced gain.
- **Outcome-model integrity** (`tests/test_outcome_model.py`): path labels, same-candle-tie
  pessimism, out-of-fold leakage guard.
- **Calibration** (`tests/test_sklearn_model.py`): Brier/ECE, probabilities sum to 1.

## Standard tests
- Features/patterns (61 candlestick detectors verified against textbook cases), risk/rules
  (every gate), sector (scoring + mapping), sentiment, tracker.

## Forward Testing tests (Sprint 1 — 128 tests)
- **M1** persistence/migrations (33), **M2** store CRUD/dedupe/stats (36), **M3**
  engine/resolver/monitor incl. cross-thread start-stop (23), **M4** `/forward/*` API incl.
  validation + 404/409/422 (19), **M5** dashboard data — analytics units, breakdown,
  live-vs-backtest status, empty/error states, static delivery (17).
- **Isolation asserted:** AST import-guard tests prove the forward-testing code and API
  import **neither** `app.ai.sklearn_model` nor `app.ai.outcome_model`.
- **Never touch production data:** all forward tests use temporary databases (`tmp_path`).
- **Coverage:** `pytest-cov` is not yet configured — no numeric coverage is claimed (see
  the Sprint 1 report's Testing section); adding a coverage gate is tracked debt.

## Principles
- **Deterministic:** synthetic data + fixed seeds; no network in unit tests.
- **Honest by construction:** a detector that never fires is a bug, not "rare" — tested
  with hand-built positive cases.
- **A change to a model/feature must ship with a report** from the honest pipeline.

## CI
- Full suite on push (Vol 25); JS syntax check for the dashboard. Target: coverage gates,
  endpoint contract tests, Playwright e2e.

## Prediction-Model / LLM integration
- Tests assert the models' output contracts and (target) that the LLM tool schema contains
  no prediction-producing tool.

## Future
- Property-based tests for the resolver/risk math; load tests (Vol 27); backtest-regression
  tests (guard the verified edge numbers from silent drift).
