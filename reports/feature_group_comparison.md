# Feature-Group Comparison — Purged Walk-Forward

Assets: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] · interval 1h · horizon 12 · 5 folds · seed 7 · bars 20000

Every set uses the SAME folds, labels, horizon, seed, and calibration. Metrics are out-of-sample, non-overlapping, pooled across assets. The production model was not touched.

| Experiment | Feats | Mean Acc | Std | Worst | Balanced | Macro-F1 | ECE | Decision |
|---|---|---|---|---|---|---|---|---|
| champion (base) | 45 | 61.18% | 3.35 | 56.62% | 41.59% | 0.408 | 0.070 | **—** |
| + multi-timeframe | 59 | 61.55% | 3.78 | 55.91% | 41.95% | 0.413 | 0.077 | **REJECT** |
| + interactions | 53 | 61.32% | 3.60 | 56.26% | 42.27% | 0.419 | 0.067 | **REJECT** |
| + market regime | 56 | 61.50% | 4.24 | 55.68% | 42.94% | 0.423 | 0.077 | **REJECT** |
| + mtf + interactions | 67 | 61.13% | 3.93 | 55.33% | 41.53% | 0.410 | 0.075 | **REJECT** |
| + all Phase-2 groups | 78 | 61.69% | 3.90 | 55.90% | 42.22% | 0.413 | 0.081 | **REJECT** |

## Decisions

- **+ multi-timeframe** → REJECT: gain +0.37pp is within noise (<0.5x fold std 3.35pp)
- **+ interactions** → REJECT: gain +0.14pp is within noise (<0.5x fold std 3.35pp)
- **+ market regime** → REJECT: gain +0.32pp is within noise (<0.5x fold std 3.35pp)
- **+ mtf + interactions** → REJECT: mean acc did not improve (-0.05pp); worst-fold dropped >1pp (-1.29pp)
- **+ all Phase-2 groups** → REJECT: gain +0.51pp is within noise (<0.5x fold std 3.35pp)
