# Volume 18 — Forward Testing (the live-proof engine) ⭐ PRIORITY #1

## Purpose
Turn the **backtest-only** Outcome-Engine edge into a **real, logged, live track record** —
the single thing standing between "verified in backtest" and any claim of real-money
viability or a credible product. **This is the most important unbuilt engine in Aegis.**

## Status: 🟢 Built (Sprint 1 COMPLETE, `v0.1.0-forward-testing`). M1 store + M2 persistence + M3 engine/resolver/monitor + M4 REST API + M5 dashboard + M6 docs are all delivered and tested (128 forward tests; full suite 361 passed). The engine is built but its **live sample is not yet accumulated** — every number remains backtest until it is. Remaining wiring (auto-record, monitor in lifespan) is tracked in the [Sprint 1 report](../sprints/sprint-01-report.md). See `sprints/sprint-01-forward-testing-plan.md`.

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

## REST API — as built (Sprint 1 · M4)
All under `/forward/*`, mounted from `app/api/forward.py` (an `APIRouter`). Each endpoint
is a thin adapter over the M2 `PredictionStore` / M3 `ForwardTestingEngine` — **no model
logic, no engine imports**. The store + engine are created once in the app lifespan and
shared via `request.app.state` (`forward_store` / `forward_engine`).

| Method · Path | Purpose | Success | Errors |
|---|---|---|---|
| `POST /forward/prediction` | Record a BUY/SELL recommendation for forward testing | `201 {prediction}` | `422` invalid body; `409` duplicate (same symbol·tf·candle·source) |
| `GET /forward/prediction/{id}` | One record by id | `200 {prediction}` | `404` unknown id |
| `GET /forward/active?symbol=` | Open predictions (PENDING/ENTRY_TRIGGERED/ACTIVE), oldest first | `200 {count, predictions}` | — |
| `GET /forward/completed?limit=&symbol=` | Resolved predictions, newest first | `200 {count, predictions}` | — |
| `GET /forward/stats?symbol=` | Aggregate R-based stats (win rate, PF, avg R, max DD, open risk) | `200 {…}` | — |
| `GET /forward/summary?symbol=` | Stats **plus** expectancy, backtest baseline, live-vs-backtest, honest confidence + disclaimer (M5-extended) | `200 {stats, expectancy, backtest, live_vs_backtest, confidence, note, disclaimer}` | — |
| `GET /forward/breakdown?by=&symbol=` | Grouped aggregates by market/sector/timeframe/confidence/regime (M5) | `200 {dimension, groups, resolved_total}` | `422` unknown dimension |

- **Request validation** (`ForwardPredictionRequest`): `recommendation` must be BUY/SELL
  (a WAIT is not a trade → `422`); `current_price`/`created_candle_ts` > 0; probabilities
  in `[0,1]`; sides are upper-cased. Rejections are FastAPI `422` with field detail.
- **Honesty built in:** `/forward/summary` returns `confidence = no_data` (0 resolved),
  `insufficient_sample` (< 50 resolved), or `building` (≥ 50), each with plain-language
  text — the API refuses to present a handful of trades as proof.
- **Not in M4** (later milestones): the dashboard panel (M5) and the auto-record path from
  the analysis pipeline + background monitor wiring. The `/track-record` naming below is
  the original sketch; the shipped surface is `/forward/*`.

## Dashboard — as built (Sprint 1 · M5)
`app/dashboard/static/forward.html`, served at `/dashboard/forward.html` (existing
StaticFiles mount) and linked from the main dashboard. **Presentation layer only** — it
consumes `/forward/*` and renders; no business logic in the browser, no direct DB access.
Six sections: **Overview** (total/active/completed, win rate, PF, avg R, expectancy, avg
holding, open risk), **Live vs Backtest** (live vs the configured backtest baseline, the
difference, a 95% CI and an honest status), **Performance Breakdown** (by market / sector /
timeframe / confidence bucket / regime), **Active Predictions**, **Completed Predictions**,
and the **Prediction Timeline** (with model + feature versions). Loading, empty, and error
states are handled; sample size is always shown so a win rate from a few trades is never
presented as proof.
- **Server-side aggregation.** Sections 2–3 need data M4 didn't expose, so the Forward API
  was extended additively: `GET /forward/breakdown` + `backtest`/`live_vs_backtest`/
  `expectancy` on `/forward/summary`. The math lives in `app/api/forward_analytics.py`
  (pure functions, no engine imports) — never in the browser.
- **Backtest baseline** is a configured constant (documented outcome-model WF result: 59.6%
  win / +0.285 avg R / PF 1.63, `reports/outcome_model_summary.md`), overridable via
  `AEGIS_FORWARD_BACKTEST_*`; a negative win rate declares "no baseline configured".
- **Honest status** on live-vs-backtest: `no_data` → `building_sample` (<30) →
  `inconclusive` / `statistically_significant` (95% CI), never over-claiming.

## API integration (original sketch — superseded by the `/forward/*` surface above)
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
