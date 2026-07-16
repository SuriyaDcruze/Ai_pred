# Volume 13 — Historical Memory

## Purpose
Store **every prediction and its real outcome, permanently** — the foundation for
learning, forward-testing, similarity, failure analysis, and the "what changed since
yesterday" experience. *"I have seen this market before."*

## Status: 🟡 Partial — `app/tracking/tracker.py` (SQLite call store). Needs a proper
prediction store (Vol 21).

## Responsibilities
- Persist each recommendation with full context and later resolve its outcome.
- Serve as the source of truth for Forward Testing (Vol 18), the Learning Engine (Vol 15),
  and the audit trail (compliance).

## Record schema (target)
For every prediction store: timestamp · asset · timeframe · **feature vector** · direction
prediction · outcome prediction (P target) · decision score · recommendation · entry/stop/
targets · market state · sector · relative strength · similarity · **actual outcome
(WIN/LOSS/OPEN)** · realised R / P&L · model version · feature version · prediction ID ·
reason summary.

## Current implementation
- `TrackedCall` (SQLite): id, created_at, symbol, timeframe, side, entry, stop, tp1/tp2,
  clicked_time/price, source (manual/ai), status (WIN/LOSS/OPEN), resolved_time/price,
  r_multiple. `CallStore` with dedupe (unique index per AI call per candle), resolve
  (stop-first pessimistic), summarize (You vs AI).

## Architecture (target)
```
Recommendation created
  → write prediction record (features + context + version stamps)
  → background resolver walks future candles → WIN/LOSS + realised R
  → aggregates: per-stock/sector/state win-rate, avg R (feeds Similarity, Learning,
    Forward Testing, Failure Analysis)
```

## API integration
- `/calls` (record/list/resolve), `/round` (You vs AI). Target: `/history` query surface
  and a prediction-store table (Postgres, Vol 21).

## Failure / logging
- Writes are atomic (SQLite/Postgres); a resolver error never loses the original record.

## Testing
- Tracker: add/dedupe/resolve/summarize covered.

## Prediction-Model integration
- Stores the model's outputs verbatim + the real result — the ground truth the Learning
  Engine trains the meta-model on.

## LLM integration
- Powers conversational memory: "why did my last trade fail?", "what changed since
  yesterday?" — the LLM reads records, never invents them.

## Compliance
- The permanent record of every recommendation shown **is** the audit trail (SEBI posture,
  Vol 03/24).

## Future
- Migrate SQLite → Postgres; add feature/model/data version stamps; retention policy;
  per-user memory (Vol 16).
