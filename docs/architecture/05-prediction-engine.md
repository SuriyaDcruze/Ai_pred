# Volume 05 — Prediction Engine (the core IP)

## Purpose
Produce the market's **directional read** — the single source of Direction, Probability,
and Confidence in Aegis. This is the proprietary core; no other component (especially no
LLM) may generate these.

## Status: 🟢 Built & validated — `app/ai/sklearn_model.py`, `app/ai/calibration.py`

## Responsibilities
- Turn the latest feature row into calibrated class probabilities: **P(up) / P(down) /
  P(neutral)**.
- Expose Direction (argmax → BUY/SELL/WAIT bias) and Confidence (calibrated max prob).
- Guarantee the confidence number is **honest** (calibrated: "60%" ≈ right 60%).
- Remain simple, fast (CPU, ~2s train), and interpretable.

## Inputs / Outputs
- **In:** OHLCV → `FeatureBuilder` → 45 features (indicators + SMC + candlesticks).
- **Out:** `ModelPrediction { p_bullish, p_bearish, p_sideways, predicted_high/low/close,
  expected_volatility, confidence }`.

## Architecture
- **Model:** calibrated **Logistic Regression** (multinomial), chosen because it **beat
  a 580K-param TCN+Transformer** by ~11 points on honest non-overlapping labels (see
  RESULTS §2). Trains in ~2s, no GPU.
- **Calibration:** isotonic (`ProbabilityCalibrator`), fit on a held-out slice — ECE
  0.14 → 0.05. This is what makes the confidence gate meaningful.
- **Labels:** cost-aware triple-barrier (`ai/dataset.py::make_labels`) — moves too small
  to beat fees are labelled NEUTRAL.
- **Artifact:** `artifacts/sklearn_model.pkl` (model + scaler + calibrator + meta).
  Per-market artifacts (e.g. NSE) are separate.

## API integration
- Consumed via `AnalysisService.predictor` → surfaced in `/analyze`, `/intelligence`,
  `/screener/nse`. Never exposed as a raw "prediction endpoint" that bypasses the
  decision/outcome layer.

## Failure / logging
- Missing artifact → graceful fallback (deep net → heuristic), each logged.
- Insufficient history → `ValueError`, surfaced as a 422 (never a fabricated number).

## Testing
- `tests/test_sklearn_model.py` — contract (probabilities sum to 1, calibrated confidence,
  drop-in for the deep Predictor), calibration metrics (Brier, ECE).
- Accuracy is measured, never asserted at an inflated level.

## Prediction-Model integration (this IS the model)
- All other engines consume `ModelPrediction`. The Outcome Engine takes its probabilities
  as *out-of-fold* features (Vol 06). The Intelligence layer reads it for the leaning.

## LLM integration
- The LLM **reads** `ModelPrediction` from the API to explain it. It has **no path** to
  produce one. This independence is the platform's core invariant (Vol 04 §3).

## Known limits (honest)
- ~59–61% directional accuracy is the **measured ceiling** — feature engineering can't
  move it (10 experiments). Direction alone is **break-even after fees**. This is *why*
  the Outcome Engine exists.

## Future
- Per-market / per-asset-group models where data supports it (validated, not assumed).
- Nightly champion/challenger retrain with promotion gates (Vol 15) — never auto-promote
  on training accuracy.
