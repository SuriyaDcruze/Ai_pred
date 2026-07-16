# Phase-2 Accuracy Improvement — Final Summary

**Verdict: no challenger promoted. Production champion unchanged. Three rounds of
feature engineering across four specs have now all landed inside the noise.**

Method: purged 5-fold walk-forward, horizon 12, seed 7, pooled across BTC/ETH/SOL,
20 000 bars each. Identical folds/labels/calibration/seed for every set. Scaling +
calibration fit inside each fold on train only. Non-overlapping validation. Leakage-
proven (11 future-invariance tests, incl. the multi-timeframe peek test).

## Results

| Feature set | Feats | Walk-forward acc | vs base | Worst fold | ECE | Decision |
|---|---|---|---|---|---|---|
| **Champion (base)** | 45 | 61.18% (±3.35pp) | — | 56.62% | 0.071 | — |
| + multi-timeframe (4h+1d) | 59 | 61.55% | +0.37pp | 55.91% | 0.077 | ❌ REJECT |
| + feature interactions | 53 | 61.32% | +0.14pp | 56.26% | 0.067 | ❌ REJECT |
| + market regime | 56 | 61.50% | +0.32pp | 55.68% | 0.077 | ❌ REJECT |
| + mtf + interactions | 67 | 61.13% | −0.05pp | 55.33% | 0.075 | ❌ REJECT |
| + all Phase-2 groups | 78 | 61.69% | +0.51pp | 55.90% | 0.081 | ❌ REJECT |

**Noise floor (0.5× fold std): 1.68pp.** Every gain is below it. The largest (+0.51pp,
"all groups") is a third of the noise and comes with the *worst* calibration (ECE
0.081) and a slightly *lower* worst fold. That is not an improvement — it is more
features fitting more noise.

## CURRENT CHAMPION (unchanged)
- **Model:** Calibrated Logistic Regression (`artifacts/sklearn_model.pkl`)
- **Mean walk-forward accuracy:** 61.18% · **Balanced:** 41.6% · **ECE:** 0.071 · **Worst fold:** 56.62%

## What Phase 2 tried (and why each was reasonable)
- **Multi-timeframe fusion** (the spec's #1): real new information (4h/1d trend), not
  a re-slice of 1h. Leakage-safe (only closed higher-TF bars). → +0.37pp, noise.
- **Feature interactions**: genuinely motivated for a *linear* model, which can't learn
  products like ADX×Volume on its own. → +0.14pp, noise.
- **Market regime** (retest): → +0.32pp, noise (consistent with the earlier round).

## The honest conclusion after three rounds
Feature engineering has been exhausted as a lever. Across four specification documents
we have now built and fairly tested **eight** feature groups — regime, price-action,
session, multi-timeframe, interactions, and their combinations — and **not one** has
produced a gain that clears the fold-to-fold noise. The champion sits at **~61%
walk-forward directional accuracy**, and that is the measured ceiling for this
approach and this data.

**This is a real finding, not a failure.** More features will not move it; each new
batch just fits noise. The remaining honest levers are *not* about predicting
direction better — they are about *trading more selectively* (the target-before-stop
outcome model, the meta-model) or accepting the ceiling and using the platform for
what it is genuinely good at: learning, discipline, and forward-testing.

Production model unchanged. README accuracy unchanged. Untouched final test NOT spent
(no genuine winner earned it).
