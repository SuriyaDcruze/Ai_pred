# Volume 18 — Forward Testing (the live-proof engine) ⭐ PRIORITY #1

## Purpose
Turn the **backtest-only** Outcome-Engine edge into a **real, logged, live track record** —
the single thing standing between "verified in backtest" and any claim of real-money
viability or a credible product. **This is the most important unbuilt engine in Aegis.**

## Status: 🔴 Not built — specified here. Recommended first implementation (Vol 04 §1).

## Why it is priority #1 (the honest core of the whole project)
Every positive number in Aegis is backtest. Winning samples are modest (97 crypto / 41
NSE). We *proved this session* that even a "great" backtest prints fantasy numbers that
won't survive real slippage. **No architecture, no feature, no model changes this.** Only
forward-testing — logging real recommendations and scoring them against real future price,
at scale, over time — can tell us if the edge is real live. Until it exists, real money
and monetisation are off the table (Vol 03).

## Responsibilities
- On a schedule (or on each analysis), record **every AI TAKE recommendation** — symbol,
  timeframe, direction, outcome probability, entry/stop/targets, market state, sector,
  full context, version stamps, timestamp.
- **Resolve** each against real future candles (stop-first pessimistic, realistic costs).
- Maintain **running live statistics**: win rate, average R, profit factor, expectancy,
  by market / sector / market-state / confidence bucket.
- **Compare live vs backtest** — is the live edge consistent with the backtest edge, or
  fading? Flag drift.
- Refuse to over-claim: report sample size and confidence intervals; "not significant"
  until enough live trades.

## Inputs / Outputs
- **In:** AI TAKE recommendations (from Intelligence/Screener), live market data.
- **Out:** a growing table of resolved live predictions + honest aggregate stats +
  live-vs-backtest comparison.

## Architecture (target)
```
Scheduler (daily, NSE close / hourly, crypto)
  → for each watched symbol / basket: run Intelligence → if TAKE, log a live prediction
  → Historical Memory (Vol 13) stores it with full context + version stamps
  → resolver walks real future candles → WIN/LOSS + realised R (costs applied)
  → aggregates: live win-rate, avg R, PF, expectancy (overall + by segment)
  → live-vs-backtest report + significance (bootstrap CI, t-stat)
  → dashboard "Live Track Record" + honest verdict
```
Reuses Paper Trading's record/resolve (Vol 17) + Historical Memory (Vol 13). The new work
is the **discipline layer**: log the AI's *TAKE decisions specifically*, at scale, and the
**honest live-vs-backtest reporting**.

## Data (Vol 21)
- A `live_predictions` table (context + outcome + version stamps); aggregate views.

## API integration (target)
- `GET /track-record` (live stats + significance), `GET /track-record/compare` (live vs
  backtest). Dashboard panel replacing the old You-vs-AI with an honest live scoreboard.

## Failure / logging
- Data gap on resolution → mark unresolved, retry; never fabricate a result.
- Duplicate prevention per candle (as in the tracker).

## Testing (target)
- Deterministic resolution on synthetic future paths; significance math; dedupe; the
  live-vs-backtest comparison logic.

## Success criteria (what "proven" means)
- **50–100+ resolved live TAKE trades** with a win rate / expectancy **consistent with
  backtest** and a confidence interval that excludes zero. Only then does "the edge is
  real live" become a defensible claim.

## Prediction-Model integration
- Logs the models' real recommendations verbatim and scores them honestly — the ultimate
  test of the core IP. No simulation, no hindsight.

## LLM integration
- The assistant reports the live record truthfully, including "too few trades to conclude"
  and "live is tracking / diverging from backtest."

## The one-line mandate
> Build the engine that proves — or honestly disproves — the edge with real, forward data.
> Everything else in Aegis is waiting on this.
