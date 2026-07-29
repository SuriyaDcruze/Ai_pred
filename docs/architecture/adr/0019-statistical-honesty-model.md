# ADR 0019 — Statistical honesty model (threshold-gated, interval-first, correction-aware)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 4)
- **Deciders:** Architecture / CTO

## Context
This project's identity (`docs/RESULTS.md`) is that **small-sample patterns are noise**. An
earlier "high-confidence bucket looked 90% / profitable" was a mirage that vanished under the real
path-dependent backtester. A naïve pattern-mining + recommendation layer is *exactly* the machine
that manufactures such fake edges — mining many conditions and reporting the best inflates false
positives. So a statistical layer is only allowed to exist if it is built to expose noise, not
generate it.

## Decision
Statistical validation (Milestone 3) is **honesty-gated**:
- **Nothing below threshold.** A pattern needs `sample_size ≥ min_sample` to be eligible; below it
  the answer is `INSUFFICIENT_DATA` (a *pass*, not a gap). On today's empty corpus that is the
  output everywhere.
- **Intervals, not point estimates.** Every rate carries a **95% Wilson confidence interval**
  (reported with width + a coarse quality label). A CI that straddles the baseline (a coin flip)
  is not actionable.
- **Significance + multiple-comparison correction.** A two-sided proportion test vs a baseline,
  then a correction applied **across the whole family** of tested patterns (Benjamini–Hochberg by
  default, Bonferroni, or none — an extensible registry; the strategy is recorded on every run,
  and the count of hypotheses tested is logged).
- **Consistency check.** Win-rate stability across chronological sub-periods flags curve-fit.
- **Promotion gate.** A pattern becomes `VALIDATED` only if it clears `min_sample` **and** is
  significant *after* correction **and** its interval excludes the baseline. Otherwise it stays
  `HYPOTHESIS`. **Weak evidence is never promoted.**
Base rollups reuse the Sprint 2 aggregate math (ADR reuse, §4.4) so the numbers cannot drift.

## Alternatives considered
- *Point estimates + a simple significance flag* — rejected: reproduces the 90%-mirage failure
  mode; a rate without an interval and without correction is exactly the noise this must expose.
- *Report best-of-K without correction* — rejected: guaranteed false-positive inflation.
- *Recompute base stats independently* — rejected: a second, drifting source of truth; reuse
  `memory_aggregates`' math (regression-asserted) instead.

## Consequences
- **Positive:** the engine says "insufficient data" honestly, never invents an edge, and the
  promotion gate is strict enough to survive the project's own scepticism.
- **Negative / accepted:** on a thin corpus almost everything is `INSUFFICIENT_DATA` — intended.
  Correction is deliberately conservative (fewer, stronger claims).
- **Enforced by:** unit tests for the CI/z-test/correction primitives; a regression test asserting
  reuse of the Sprint 2 aggregate values; threshold/hypothesis/validated classification tests;
  an empty/thin-corpus honesty test.
