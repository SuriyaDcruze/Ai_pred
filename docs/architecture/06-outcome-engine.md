# Volume 06 — Outcome Engine (the verified edge)

## Purpose
Decide **whether a trade the Prediction Engine wants is worth taking** — by predicting
"will this trade reach its target before its stop?" This is meta-labelling (López de
Prado), and it is **the first and only verified edge in Aegis.**

## Status: 🟢 Built & verified — `app/ai/outcome_model.py`, `app/training/outcome_training.py`

## Why it exists
Direction accuracy ≠ profit. A 60%-accurate model is break-even because the stop is hit
first, the move is too small, or fees eat it. The Outcome Engine attacks that gap
directly: instead of predicting direction *better* (impossible past ~61%), it **selects
trades better.**

## Responsibilities
- Given a directional setup + market features, output **P(target-first)** and a
  **TAKE / VETO** decision against a threshold.
- Default to **VETO/WAIT** when not confident ("fewer high-quality trades beat many weak").
- Provide per-market models (crypto vs NSE) — each market its own artifact.

## Inputs / Outputs
- **In:** base features + **out-of-fold** direction probabilities (entropy, margin).
- **Out:** `{ p_target, p_stop, threshold, take, verdict }`.

## Architecture
- **Model:** `HistGradientBoostingClassifier` on outcome features.
- **Labels:** path-dependent target-before-stop (`outcome_labels`) — scans future
  highs/lows in order; same-candle TP+SL resolved **pessimistically** (assume stop).
- **Anti-leakage (critical):** the direction probabilities used as features are generated
  **out-of-fold** (`oof_direction_probs`) — never from a model trained on the same rows.
- **Artifacts:** `artifacts/outcome_model.pkl` (crypto), `outcome_model_nse.pkl` (NSE).

## Verified results (backtest — honest)
| Test | Take-all | **Filtered** |
|---|---|---|
| Non-overlapping walk-forward (crypto) | −0.011R | **+0.225R (PF 1.47)** |
| Untouched final test (crypto) | +0.035R | **+0.482R (PF 2.31)** |
| NSE daily, untouched | +0.07R | **+0.84R (PF 6.9)** (n=41, modest) |

Why believed: **monotonic threshold sweep** (real-signal fingerprint), held across
BTC/ETH/SOL, **survived non-overlapping + untouched tests**, robust across horizons 8–24,
and **generalises to Indian stocks** — the tests that killed earlier leads.

## API integration
- `AnalysisService.assess_outcome()` → `/analyze` (`outcome` field), `/outcome`,
  `/intelligence`, `/screener/nse`. It is the **decision layer**: BUY/SELL only when
  direction≠WAIT **and** outcome=TAKE.

## Failure / logging
- Never raises into the signal path — a failed assessment degrades to "unavailable", core
  signal still renders.

## Testing
- `tests/test_outcome_model.py` — path labels, same-candle-tie pessimism, out-of-fold
  leakage guard, feature builder shape.

## LLM integration
- The LLM explains the TAKE/VETO and the P(target) — never computes it.

## Known limits (honest)
- **Backtest-only. Zero live trades.** Winning samples modest (97 crypto / 41 NSE).
  Compounding % from the sim is fantasy (idealised fills); trust the **per-trade R**.
  The Forward-Testing engine (Vol 18) is what turns this into live proof.

## Future
- Retrain on the live track record as it accumulates (Vol 15); per-sector outcome models
  once data supports; richer cost modelling for NSE slippage.
