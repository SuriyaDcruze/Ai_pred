# Feature-Group Comparison — Purged Walk-Forward

Assets: BTCUSDT, ETHUSDT, SOLUSDT · interval 1h · horizon 12 · 5 folds · seed 7 · bars 20000

Every set uses the SAME folds, labels, horizon, seed, and calibration. Metrics are out-of-sample, non-overlapping, pooled across assets. The production model was not touched.

Decisions apply an **uncertainty gate** (a gain smaller than 0.5× the fold std is noise) and a **class-balance gate** (a gain from predicting one direction more is not real). Under those honest gates, **no challenger passes.**

| Experiment | Feats | Mean Acc | Std | Worst | Balanced | Macro-F1 | ECE | Decision |
|---|---|---|---|---|---|---|---|---|
| champion (base) | 45 | 61.01% | 2.59 | 57.50% | 41.30% | 0.405 | 0.070 | **—** |
| + market regime | 56 | 61.37% | 2.95 | 57.40% | 41.68% | 0.408 | 0.068 | **REJECT** |
| + price action | 71 | 60.03% | 2.20 | 57.10% | 40.96% | 0.399 | 0.071 | **REJECT** |
| + session | 58 | 61.47% | 2.54 | 58.10% | 42.02% | 0.407 | 0.073 | **REJECT** |
| + regime + price action | 82 | 60.32% | 2.62 | 57.64% | 41.62% | 0.407 | 0.069 | **REJECT** |
| + all groups | 95 | 59.13% | 3.61 | 53.25% | 41.01% | 0.398 | 0.081 | **REJECT** |

## Decisions

- **+ market regime** → REJECT: gain +0.36pp is within noise (<0.5x fold std 2.59pp)
- **+ price action** → REJECT: mean acc did not improve (-0.98pp)
- **+ session** → REJECT: gain +0.46pp is within noise (<0.5x fold std 2.59pp); gain looks like class imbalance (UP-rec -0.02 vs DOWN-rec +0.03)
- **+ regime + price action** → REJECT: mean acc did not improve (-0.69pp)
- **+ all groups** → REJECT: mean acc did not improve (-1.88pp); worst-fold dropped >1pp (-4.25pp)

**Result:** champion unchanged. Final untouched test NOT run (no genuine winner to spend it on).
