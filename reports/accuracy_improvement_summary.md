# Accuracy Improvement — Final Summary

**Verdict: no challenger promoted. The production champion is unchanged.**

Method: purged expanding-window walk-forward, 5 folds, horizon 12, seed 7, pooled
across BTCUSDT / ETHUSDT / SOLUSDT, 20 000 bars each. Every feature set ran through
identical folds, labels, calibration, and seed. Scaling + calibration fit inside each
fold on training data only. Non-overlapping validation samples. No look-ahead
(8 future-invariance tests passing). **The untouched final test period was NOT used** —
the spec says to spend it only after selecting a genuine winner, and there is none.

---

## CURRENT CHAMPION
- **Model:** Calibrated Logistic Regression (unchanged — `artifacts/sklearn_model.pkl`)
- **Mean walk-forward accuracy:** 61.01% (fold std ±2.59pp)
- **Balanced accuracy:** 41.3%
- **Macro F1:** 0.405
- **ECE:** 0.070
- **Worst-fold accuracy:** 57.50%

## CHALLENGER 1 — Current + market-regime features
- **Mean walk-forward accuracy:** 61.37%
- **Change from champion:** **+0.37pp**
- **Balanced accuracy:** 41.7% · **Macro F1:** 0.408 · **ECE:** 0.068 · **Worst fold:** 57.40%
- **Decision: REJECT** — the +0.37pp gain is 0.14× the fold std (±2.59pp), i.e. inside
  the noise. Not a stable improvement.

## CHALLENGER 2 — Current + normalized price-action features
- **Mean walk-forward accuracy:** 60.03%
- **Change from champion:** **−0.98pp**
- **Balanced accuracy:** 41.0% · **Macro F1:** 0.399 · **ECE:** 0.071 · **Worst fold:** 57.10%
- **Decision: REJECT** — mean accuracy declined. These features add noise, not signal.

## CHALLENGER 3 — Current + session/time features
- **Mean walk-forward accuracy:** 61.47%
- **Change from champion:** **+0.47pp**
- **Balanced accuracy:** 42.0% · **Macro F1:** 0.407 · **ECE:** 0.073 · **Worst fold:** 58.10%
- **Decision: REJECT** — twice rejected: (a) the +0.47pp gain is 0.18× the fold std
  (noise), and (b) it comes from **class imbalance** — DOWN-recall rose (+0.03) while
  UP-recall fell (−0.02), i.e. it just predicted DOWN more in a market that fell. That
  is the spec's explicit reject condition, not a real edge.

## BEST FEATURE COMBINATION
- **Selected feature groups:** none.
- **Reason:** every combination of the "accepting" groups **degraded** performance
  (regime + price-action −0.68pp; all groups −1.88pp with worst-fold −4.25pp). Real
  independent signal stacks; noise cancels. The combinations degrading is strong
  evidence the individual +0.4pp gains were luck, not information.
- **Ready for final untouched test:** **NO.** Nothing earned it.

---

## Honest conclusion

The three new feature groups (market-regime, price-action, session) do **not** provide
a robust, out-of-sample improvement to directional accuracy. The two that showed a tiny
positive mean gain (+0.37pp, +0.47pp) both failed the uncertainty test — the gains are
~7× smaller than the fold-to-fold spread — and the session gain additionally came from
class imbalance rather than better prediction.

This is a valid and important result, exactly as the spec anticipated: *"Do not describe
a result as improved unless unseen validation metrics improve."* They didn't. The
production model stays at its measured ~61% walk-forward directional accuracy (which,
per `docs/RESULTS.md`, is still **break-even after fees** — accuracy is not profit).

**What was kept:** the new feature modules, the purged walk-forward harness, the leakage
tests, and this comparison pipeline — all reusable tooling for the next honest experiment
(the target-before-stop outcome model, which attacks profitability directly rather than
accuracy). **What was not changed:** the production model, the README's accuracy number.
