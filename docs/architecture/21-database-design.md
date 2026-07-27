# Volume 21 — Database Design

## Purpose
Define persistence: what is stored, in what schema, and the migration path from the
current SQLite tracker to a Postgres store that supports Historical Memory, Forward
Testing, and multi-user.

## Status: 🟡 Growing — SQLite. `data/prediction_history.db` is the permanent store
(Sprint 1 Forward Testing + Sprint 2 Historical Memory schema); legacy `data/calls.db`
(You-vs-AI tracker) unchanged. Postgres remains the future path.

## Current
- **SQLite** `data/calls.db`: `calls` table (TrackedCall) with a unique partial index for
  AI-call dedupe per candle. Rules stored in a `rules` table. Good enough for single-user.
- **SQLite** `data/prediction_history.db`: the permanent memory, evolved only by
  **append-only, versioned, idempotent** migrations (`app/database/migrations.py`):
  - `0001` **`predictions`** (Sprint 1) — the canonical, immutable fact table.
  - `0002` **`memory_reasoning`** — the "why" (rationale/factors/rule-check), 1:1 with a
    prediction; index on `confidence`.
  - `0003` **`memory_embeddings`** — vector placeholder for Similarity (Vol 14); unique
    `(prediction_id, embedding_kind)`, index on `embedding_kind`; `vector` NULL until filled.
  - `0004` **`memory_aggregates`** — derived performance rollups, PK
    `(dimension, bucket, model_version)`; index on `dimension`.
  - `0005` additive **retrieval indexes** on `predictions` (`(sector,status)`,
    `(market_regime,status)`, `(prediction_model_version,status)`, `(timeframe,created_at)`)
    — metadata only; existing indexes untouched.
- **Design note (Sprint 2):** Historical Memory uses **satellite tables** keyed on
  `prediction_id`, never a copy of `predictions` (ADR 0007). The composed *Memory Record* is a
  read model assembled by the Retrieval Engine (ADR 0008). This keeps `predictions` immutable,
  every change additive, and rollback a clean table drop. Migration tests verify a fresh DB, a
  populated Sprint-1 DB upgrade (predictions byte-for-byte unchanged), idempotency, and
  rollback safety.

### Relationships & migration sequence (as built, Sprint 2)
```
predictions (1) ──┬── (0..1) memory_reasoning        [PK prediction_id]
                  └── (0..n) memory_embeddings        [UNIQUE (prediction_id, embedding_kind)]

memory_aggregates ── derived from predictions (no FK; PK (dimension,bucket,model_version))
```
- FKs reference `predictions(prediction_id)` (`PRAGMA foreign_keys=ON`); satellites are only
  written for predictions that exist. Aggregates have no FK — they are rebuildable rollups.

| Version | Name | Effect |
|---|---|---|
| `0001` | create_predictions | Sprint 1 fact table + its indexes |
| `0002` | create_memory_reasoning | + `memory_reasoning` (idx on `confidence`) |
| `0003` | create_memory_embeddings | + `memory_embeddings` (unique kind idx) |
| `0004` | create_memory_aggregates | + `memory_aggregates` (idx on `dimension`) |
| `0005` | memory_retrieval_indexes | + additive indexes on `predictions` (no table change) |
| `0006` | *(reserved)* | `memory_news` — deferred/optional |

- **Version compatibility:** migrations are forward-only, append-only, idempotent, recorded in
  `schema_migrations`. Older code that predates a satellite keeps working (the tables are
  additive); readers use by-name column access + a per-record `schema_version`, so future
  nullable columns never break existing readers. A model swap never blends performance — the
  aggregate key includes `model_version`.
- **Schema diagram:** see `sprints/sprint-02-historical-memory-plan.md` §4.5 and Volume 13.

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
