# ADR 0004 — Forward testing before production / real money

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 1)
- **Deciders:** Architecture / CTO

## Context
Every positive performance number in Aegis is **backtest-only**, on modest samples (97
crypto / 41 NSE untouched trades). We proved this session that even an excellent backtest
can print fantasy numbers (idealised fills, compounding amplifying error) that will not
survive real slippage and fees. The outcome-model edge is real in backtest but **unproven
live**. Shipping predictions as tradeable — or marketing the product on backtest numbers —
before live evidence would be dishonest and risky.

## Decision
**No real-money trading and no "proven live" claim until a forward-tested live track record
exists.** Build the Forward Testing Engine first (Sprint 1): record every actionable
recommendation with full context, resolve it against real future price, and report results
**with sample size and honest confidence** — never presenting a handful of trades as proof.
The dashboard explicitly distinguishes live from backtest and states when the sample is too
small to conclude.

## Consequences
- **Positive:** claims stay defensible; the product's credibility rests on evidence, not
  optimistic backtests; a real audit trail accumulates (also a compliance asset).
- **Positive:** honesty is enforced in code — `/forward/summary` returns
  `no_data` / `building_sample` / `inconclusive` / `statistically_significant`, not a bare
  win rate.
- **Negative / accepted:** "proven live" is weeks-to-months away; monetisation waits on
  evidence. This is the honest cost and is accepted deliberately.
- **Definition of done for "real":** 50–100+ resolved live trades whose win rate /
  expectancy is consistent with backtest and whose confidence interval excludes a coin flip.
