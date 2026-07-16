# Outcome Model — Trade-Selection Results

Assets ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] · 5-fold purged walk-forward · horizon 12 · threshold P(target)≥0.55

The question: does filtering by the outcome model beat taking every direction signal? Judged on **expectancy after cost**, not accuracy.

| Strategy | Trades | Win rate | Avg R (net) | Profit factor | Total R |
|---|---|---|---|---|---|
| Take every direction signal | 42386 | 47.1% | -0.025 | 0.96 | -1039.7 |
| **Filtered by outcome model** | 8009 | 59.6% | +0.285 | 1.63 | +2280.0 |

## Threshold sweep (pooled)
| P(target)≥ | Trades | Win rate | Avg R (net) | Profit factor |
|---|---|---|---|---|
| 0.40 | 19935 | 51.4% | +0.082 | 1.15 |
| 0.45 | 13499 | 54.7% | +0.161 | 1.32 |
| 0.50 | 9963 | 57.7% | +0.235 | 1.49 |
| 0.55 | 8009 | 59.6% | +0.285 | 1.63 |
| 0.60 | 6510 | 60.7% | +0.309 | 1.70 |
| 0.65 | 5076 | 61.4% | +0.323 | 1.74 |
| 0.70 | 3592 | 61.5% | +0.327 | 1.75 |

## Verdict: ACCEPT — filtering improves expectancy

Accepted only if filtered expectancy, profit factor, and win rate all improve and survive the folds. The production direction model is untouched either way.

---

## Verification — non-overlapping + untouched final test (P(target)≥0.60)

The overlapping walk-forward number could be inflated by correlated trades, so we
re-ran two disciplined checks. **Both passed** — this is the difference between a
real edge and an artifact (the earlier high-confidence bucket failed exactly here).

| Check | Strategy | Trades | Avg R (net) | Profit factor |
|---|---|---|---|---|
| Non-overlapping, walk-forward | take-all | 3,549 | −0.011 | 0.98 |
| Non-overlapping, walk-forward | **filtered** | 540 | **+0.225** | **1.47** |
| **Untouched final test** (never in any fold) | take-all | 747 | +0.035 | 1.06 |
| **Untouched final test** | **filtered** | 97 | **+0.482** | **2.31** |

**Verdict: ACCEPT (verified).** Filtering by the outcome model turns break-even
direction signals into positive expectancy, and it survives non-overlapping sampling
*and* the untouched final test — the tests that killed the confidence-bucket idea.

**Honest caveats:** the final-test filtered sample is 97 non-overlapping trades
(modest); this is R-expectancy, not a full compounding backtest; validated on
crypto/1h only; cost assumed flat. **Needs live forward-testing before real money.**
The production direction model is unchanged — the outcome model is a separate veto layer.
