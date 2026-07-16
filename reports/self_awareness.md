# Self-Awareness — where the outcome-model edge is real (and where it isn't)

Assets ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] · non-overlapping walk-forward · threshold 0.6

**Overall:** take-all -0.011R (n=3549) → filtered +0.225R, 57% win, PF 1.47 (n=540).

Filtered trades broken down by condition. A segment where avg R stays clearly positive is where the edge is real; near-zero means the model should be more cautious there — *this is the AI's self-knowledge of when not to trade.*

## By trend (ADX)

| Bucket | Trades | Win rate | Avg R |
|---|---|---|---|
| weak <18 | 127 | 59% | +0.271 |
| moderate 18-25 | 142 | 63% | +0.315 |
| strong >25 | 271 | 54% | +0.157 |

## By volatility (ATR%)

| Bucket | Trades | Win rate | Avg R |
|---|---|---|---|
| low <1% | 383 | 55% | +0.170 |
| normal 1-2.5% | 149 | 61% | +0.328 |
| high >2.5% | 8 | 88% | +0.985 |

## By direction confidence

| Bucket | Trades | Win rate | Avg R |
|---|---|---|---|
| low <45% | 43 | 44% | -0.099 |
| mid 45-55% | 104 | 38% | -0.254 |
| high >55% | 393 | 64% | +0.388 |

## By session (UTC)

| Bucket | Trades | Win rate | Avg R |
|---|---|---|---|
| Asia 21-7 | 202 | 58% | +0.249 |
| EU 7-13 | 78 | 59% | +0.276 |
| US 13-21 | 260 | 56% | +0.192 |

## Self-knowledge summary

- 🟢 **Edge strong:** trend (ADX): weak <18 (+0.271R, n=127)
- 🟢 **Edge strong:** trend (ADX): moderate 18-25 (+0.315R, n=142)
- 🟢 **Edge strong:** trend (ADX): strong >25 (+0.157R, n=271)
- 🟢 **Edge strong:** volatility (ATR%): low <1% (+0.170R, n=383)
- 🟢 **Edge strong:** volatility (ATR%): normal 1-2.5% (+0.328R, n=149)
- 🟢 **Edge strong:** direction confidence: high >55% (+0.388R, n=393)
- 🟢 **Edge strong:** session (UTC): Asia 21-7 (+0.249R, n=202)
- 🟢 **Edge strong:** session (UTC): EU 7-13 (+0.276R, n=78)
- 🟢 **Edge strong:** session (UTC): US 13-21 (+0.192R, n=260)
- 🔴 **Edge weak / avoid:** direction confidence: low <45% (-0.099R, n=43)
- 🔴 **Edge weak / avoid:** direction confidence: mid 45-55% (-0.254R, n=104)

---

## Was the confidence gate actionable? Tested → NO (honest)

The dev-data segmentation suggested refusing trades below ~55% direction confidence.
We tested that on the **untouched final test** (non-overlapping):

| Strategy | Trades | Avg R | Profit factor |
|---|---|---|---|
| Outcome filter only | 97 | +0.482 | 2.31 |
| Outcome + confidence ≥ 0.55 | 94 | +0.482 | 2.31 |

**The gate is redundant** — it removed only 3 trades and changed nothing. The outcome
model already avoids low-confidence-direction trades on its own; the dev-data pattern
did not translate into an out-of-sample improvement. So we do **not** add the gate.

This analysis remains valuable for **explainability** (showing *why* a setup is strong
or weak by condition), but it found no additional tradeable edge. The Outcome Model
(Phase 3) is the edge; it already captures the confidence effect internally.
