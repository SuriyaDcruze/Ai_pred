# How the model works — and the honest truth about accuracy

> **This document was rewritten after a hard lesson.** We spent most of the project
> on a 580,000-parameter TCN+Transformer. Then we did what the FOREX MASTER MODEL
> CONTEXT told us to do from the start — compared it against simple baselines on
> honest labels — and a **plain logistic regression beat it by ~11 points**. The
> deep net is gone. What follows describes what actually ships.

## 1. What the model sees
Every candle is turned into **45 numbers**: returns, RSI, MACD, ATR, ADX, VWAP,
Bollinger width, SuperTrend, SMC structure (BOS/CHoCH/FVG/order blocks), and
**candlestick patterns** (Hammer, Engulfing, Doji, Stars, Pin Bars…). The
production model reads the **latest bar's** 45 features. (The chart still shows all
61 candlestick patterns for you to read — see below.)

## 2. What it predicts
- **P(up)**, **P(down)**, **P(sideways)** — direction probabilities that are now
  **calibrated** (see §6), so "60%" really means ~60%.
- predicted **high / low / close** band (derived from ATR × √horizon)
- expected **volatility**
- a **confidence** score = the probability of the class it is actually calling

The target is defined per the PDF: **UP/DOWN/NEUTRAL over the next 12 candles**,
with the NEUTRAL band **floored at trading cost** — a move too small to beat fees is
labelled NEUTRAL, so the model is never trained to chase unprofitable wiggles.

## 3. How it decides to trade
A prediction is **not** a trade. A trade fires when a **majority of confirmations**
agree (trend · volume · momentum · structure · candlestick — **3 of 5**), **and**
confidence ≥ the gate, **and** risk:reward ≥ 2. Otherwise **WAIT**.

> The old gate demanded *all five* confirmations plus 80% confidence, and measured
> on 500 live candles it fired **zero** trades — the confidence number was
> uncalibrated and never reached 80%, so the door was mathematically shut. That was
> a bug, not caution. See §6.

## 4. How "accuracy" is measured
**Directional accuracy** out-of-sample, on **non-overlapping** labels. That second
part matters: with a 12-bar horizon, neighbouring bars share 11 of 12 future
candles, so consecutive labels are ~92% identical. Scoring every consecutive bar
rewards a model for repeating its last answer and inflates the number badly (we
measured a naive rule at a fake 63% that dropped to 48% once the overlap was
purged). Every accuracy figure here is measured on disjoint windows.
- **50%** = a coin flip · **>55%** = a possible edge · **>58%** = notable

## 5. The honest result
The baseline race on BTC 1h (cost-aware, non-overlapping, out-of-sample):

| Model | Directional accuracy | Brier |
|---|---|---|
| **Logistic Regression (ships)** | **~59%** | ~0.47 |
| XGBoost | ~58% | ~0.48 |
| Random Forest | ~58% | ~0.47 |
| Always-UP (no model) | ~51% | — |
| Deep net (580K params) | ~48% | ~0.63 |

Two honest caveats that keep this in perspective:
- **~59% on the test slice is encouraging, not proven.** The test set is small, and
  a single symbol/period. The number to trust is the live forward-test (§8).
- The deep net genuinely **could not learn direction** here: retrained on the exact
  target, its validation *direction* loss stayed at ~0.93 vs 1.099 for pure chance —
  it barely moved off random. When a *linear* model is your best, that's a strong
  hint there is no rich non-linear structure to find, only a thin linear signal.

## 6. Calibration — why the confidence number finally means something
A model's raw probability output is **not** a probability until it's calibrated.
Ours wasn't, and it cost us a day: when the raw model said "54% sure" it was
actually right **76%** of the time — wildly *under*-confident — so an 80% gate could
never open. We now fit **isotonic regression** on a held-out slice (never the test
set). Result on BTC 1h: calibration error (ECE) fell from **0.138 → 0.05**. Accuracy
is unchanged by design — calibration makes the confidence *honest*, not the model
smarter, and that is the whole point.

## 7. What to do while the edge is thin
- **📰 News sentiment** — the one input not derived from price. A candidate edge,
  informs *you* today; would need to become a model feature (and a retrain) to help
  the model. Not proven.
- **✅ My Rules** — a discipline layer, not an accuracy one. It stops you taking the
  trades you already know you shouldn't.
- **Meta-model** (`app/training/meta.py`) — learns from your live Track Record which
  setups actually win, and vetoes the rest. Needs ~200 resolved calls first.
- **Nightly retrain** (`app/scripts/nightly_retrain.py`) — champion/challenger: a new
  model must *beat* the incumbent out-of-sample before it's allowed to replace it.

Beware of anything promising to fix accuracy with a plug-in. Two "TradingView MCP"
projects were evaluated for this repo; **neither contains a predictive model** —
they are indicator relays. Data plumbing is not an edge.

## 8. The one number to trust
Not the training loss, not a single backtest, not the ~59% on a small test slice —
the **forward-tested win rate** in the dashboard's 📓 Track Record, over **many**
calls. That's the truth about whether it works, on live data you can't overfit to,
and it's why auto-log runs server-side even when the browser is closed.

## 9. How to reproduce all of this
```bash
python -m app.training.baselines --symbol BTCUSDT --interval 1h --bars 20000   # the race
python -m app.ai.calibration     --symbol BTCUSDT --model logistic             # calibration report
python -m app.ai.sklearn_model   --symbol BTCUSDT --bars 20000                 # train the ship model
```
The winner is saved to `artifacts/sklearn_model.pkl` and picked up automatically by
`AnalysisService` ahead of any deep-net checkpoint.
