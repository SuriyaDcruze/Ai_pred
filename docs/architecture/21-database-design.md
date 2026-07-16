# Volume 21 — Database Design

## Purpose
Define persistence: what is stored, in what schema, and the migration path from the
current SQLite tracker to a Postgres store that supports Historical Memory, Forward
Testing, and multi-user.

## Status: 🟡 Minimal — SQLite via `app/tracking/tracker.py`; `app/database/models.py` stub.

## Current
- **SQLite** `data/calls.db`: `calls` table (TrackedCall) with a unique partial index for
  AI-call dedupe per candle. Rules stored in a `rules` table. Good enough for single-user.

## Target schema (Postgres)
```
users(id, email, created_at, ...)                         ← Vol 16
user_preferences(user_id, key, value)
watchlists(user_id, symbol)

predictions(                                              ← Vol 13 (the core store)
  id, user_id?, ts, asset, timeframe,
  direction, p_up, p_down, p_neutral, confidence,
  outcome_p_target, decision, recommendation,
  entry, stop, tp1, tp2,
  market_state, sector, rel_strength, similarity_json,
  reason_summary,
  model_version, feature_version, data_version,           ← reproducibility
  status(OPEN/WIN/LOSS), resolved_ts, resolved_price, realised_r)

live_predictions  (same shape, flagged as forward-test)   ← Vol 18
sector_snapshots(ts, sector, score, label, rank)          ← optional history
news_items(ts, symbol, title, sentiment, impact, scope)   ← Vol 10 (future)
```

## Design principles
- **Immutable prediction records** — a recommendation, once shown, is never edited (audit
  trail / compliance, Vol 03/24); resolution writes to status/resolved_* only.
- **Version stamps** on every prediction (model/feature/data) so any result is
  reproducible and the Learning Engine can segment by version.
- **Indexes** on (asset, ts), (user_id, ts), (status) for the resolver & aggregates.
- **Aggregate views/materialised views** for track-record stats (by market/sector/state/
  confidence bucket).

## Migration path
1. Keep SQLite for single-user paper trading now.
2. Introduce SQLAlchemy models + Alembic migrations (`app/database/`).
3. Move to Postgres when multi-user / forward-testing scale requires it.
4. Backfill existing `calls` into `predictions`.

## Failure handling
- Atomic writes; the resolver is idempotent; a failed resolution retries, never corrupts
  the original record.

## Testing
- Schema round-trips; dedupe constraints; resolver idempotency; aggregate correctness.

## Security / privacy
- PII isolated in `users`; per-user row isolation; retention & deletion (DPDP/GDPR-aware).

## Future
- Time-series store for candles if we cache history; partitioning by date at scale;
  read replicas for analytics.
