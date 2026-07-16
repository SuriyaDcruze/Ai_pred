# Compounding Backtest — and why you must NOT believe the headline number

Running the outcome-filtered strategy as a compounding equity curve (one position at
a time, 1% risk/trade) produced numbers that are **too good to be true**, and that is
exactly why this report exists — to stop anyone (including us) from believing them.

## The headline (DO NOT TRUST)
| Asset | Take-all return | Filtered return | Filtered max DD | Sharpe |
|---|---|---|---|---|
| BTC | +53% | +3,357% | 10% | 11.0 |
| ETH | +165% | +7,329% | 14% | 13.1 |
| SOL | +116% | +10,925% | 5% | 15.4 |

## Why these are NOT real
- **Sharpe 11-15 is a fantasy.** Renaissance Medallion ≈ 2. Buffett ≈ 0.8. Nothing
  real sits at 13.
- **Compounding amplifies error exponentially.** Our honest per-trade edge is ~+0.2 to
  +0.48R. Compounding ~800 trades turns a *tiny* overestimate into a huge fake return.
  The compounded % is hypersensitive and is NOT a robust measure of edge.
- **The R-model is idealized** — perfect stop/target fills, no slippage, no gaps, no
  fat tails. Real crypto gaps through stops. Real drawdowns are far worse than 10%.
- **It compounds all dev out-of-fold trades**, which are more optimistic than the
  truly-untouched slice.

## What IS real
Filtered beats take-all on **every** asset — higher win rate (66-70% vs ~48%), higher
profit factor (2-3 vs ~1.1), lower drawdown. That *direction* is consistent with the
verified edge. **The edge is real; the magnitude is not.** The trustworthy number
remains the non-overlapping untouched-final-test expectancy (~+0.48R, PF 2.31), and
even that needs LIVE forward-testing before real money. A backtest is not a track record.
