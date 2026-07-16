# Volume 17 — Paper Trading

## Purpose
Let a user (or the AI) **take positions on paper** and have them scored against real
future price — risk-free practice and the raw material for a live track record.

## Status: 🟡 Partial — `app/tracking/tracker.py` (records + resolves calls; You-vs-AI).

## Responsibilities
- Record a paper trade (user- or AI-initiated) with entry/stop/targets at a real
  timestamp/price.
- **Resolve** it by walking future candles (stop-first pessimistic) → WIN/LOSS + realised
  R, with no look-ahead.
- Maintain a running scoreboard (You vs AI; per-source stats).

## Inputs / Outputs
- **In:** symbol, timeframe, side, entry, stop, tp1/tp2, source (manual/ai).
- **Out:** a `TrackedCall` that resolves to WIN/LOSS/OPEN with r_multiple; summary stats.

## Architecture
- `CallStore` (SQLite) with dedupe; `resolve_call()` (pessimistic fills, fees implicit in
  R); `summarize()` (overall + ai + manual). Server-side auto-log loop can record the AI's
  picks browser-independently.

## Relationship to Forward Testing (Vol 18)
- Paper Trading is the **mechanism** (record + resolve). Forward Testing is the
  **discipline** — logging the *AI's TAKE decisions specifically*, at scale, over weeks,
  to produce statistically meaningful **live proof** of the Outcome-Engine edge.

## API integration
- `/calls`, `/calls/ai`, `/round`, `/calls/export`, auto-log config `/autolog`.

## Failure / logging
- Dedup prevents double-logging a candle; resolver tolerates gaps.

## Testing
- Tracker add/dedupe/resolve/summarize.

## Prediction-Model integration
- Logs the model's actual recommendations and their real results — never a simulated
  "what the model might have said."

## LLM integration
- The assistant reports the paper record honestly ("AI: 12 W / 15 L so far — too few to
  conclude") and can explain a specific failed trade.

## Future
- Per-user paper portfolios; Groww-workflow logging (note → paper → track); equity curve;
  feed into the Learning Engine once samples are large enough.
