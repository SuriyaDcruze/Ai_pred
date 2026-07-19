# Sprint 1 · Milestone 1 — Schema + Prediction Models + Migration (plan, awaiting approval)

> Scope of M1 only: the **long-term database schema**, the **Prediction domain models**,
> the **migration mechanism**, and **unit tests**. Then STOP for review.
> No store CRUD (M2), no engine/resolver (M3), no API (M4), no dashboard (M5).

## Adjustments incorporated (from the approval)
- DB is **`data/prediction_history.db`** — the *permanent* store for Forward Testing,
  Historical Memory, Learning, Similarity, GPT history, and Model Registry (future).
  Designed so **new tables are added via migrations** without breaking compatibility.
- **Rich records:** market regime, market phase, sector, session, volatility bucket,
  similarity score, decision score + a forward-compatible `context_json` blob.
- **Version everything independently:** `prediction_model_version`,
  `outcome_model_version`, `feature_version` — separate columns; new versions never break
  the schema.

## 1. Implementation plan (M1)
Build the persistence foundation with **no behavioural coupling** to the Prediction/
Outcome engines (they are read-only dependencies, untouched):
1. A small **raw-sqlite3 database layer** (`app/database/`) — connection + a lightweight,
   idempotent, versioned **migration runner**. Matches the existing `tracker.py` style
   (raw sqlite3), zero new dependencies. (The unused SQLAlchemy stub stays as the optional
   Postgres path for Vol 21 — nothing imports it, no conflict.)
2. The **domain models** (`app/forward_testing/models.py`) — `PredictionStatus` (8-state
   enum) and `PredictionRecord` (rich dataclass) with `to_row()` / `from_row()` mapping
   and `is_open()` / `is_terminal()` helpers.
3. **Migration 0001** creates the `predictions` table + indexes.
4. **Unit tests** for the enum, the record round-trip, and the migration runner.

## 2. Files (M1 — all new; nothing existing modified)
| File | Responsibility |
|---|---|
| `app/database/connection.py` | `get_connection(path=DEFAULT_DB)` → sqlite3 conn (WAL, busy_timeout, row_factory); `DEFAULT_DB = data/prediction_history.db` |
| `app/database/migrations.py` | `run_migrations(conn)` — idempotent, versioned; `MIGRATIONS` list; `schema_migrations` tracking; migration `0001_create_predictions` |
| `app/database/__init__.py` | exports `get_connection`, `run_migrations` (currently empty) |
| `app/forward_testing/__init__.py` | package |
| `app/forward_testing/models.py` | `PredictionStatus`, `PredictionRecord`, `to_row`/`from_row`, helpers |
| `tests/test_prediction_history.py` | unit tests (models + migration) |

**Not touched in M1:** `api/main.py`, `service.py`, `config.py`, dashboard, tracker,
Prediction/Outcome engines. (Config/API wiring arrives in later milestones when needed.)

## 3. Database schema (`data/prediction_history.db`)
```sql
CREATE TABLE schema_migrations (
  version     INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  applied_at  TEXT NOT NULL
);

CREATE TABLE predictions (
  prediction_id       TEXT PRIMARY KEY,           -- uuid4
  created_at          TEXT NOT NULL,              -- ISO8601 UTC
  updated_at          TEXT NOT NULL,
  created_candle_ts   INTEGER NOT NULL,           -- epoch secs of the origin bar
  -- instrument
  symbol      TEXT NOT NULL,
  exchange    TEXT NOT NULL,
  timeframe   TEXT NOT NULL,
  source      TEXT NOT NULL DEFAULT 'forward',    -- forward | manual | screener
  -- prediction outputs (from the models; read-only, verbatim)
  current_price   REAL NOT NULL,
  direction       TEXT NOT NULL,                  -- BUY | SELL | WAIT
  direction_prob  REAL,
  outcome_prob    REAL,
  decision_score  REAL,
  recommendation  TEXT NOT NULL,                  -- BUY | SELL | WAIT
  -- trade plan (from Risk Engine)
  entry REAL, stop REAL, target1 REAL, target2 REAL,
  -- rich market context (explainability + future learning)
  market_regime     TEXT,
  market_phase      TEXT,
  sector            TEXT,
  session           TEXT,
  volatility_bucket TEXT,
  similarity_score  REAL,
  context_json      TEXT,                          -- full context blob (forward-compatible)
  -- versions (independent; forward-compatible)
  prediction_model_version TEXT,
  outcome_model_version    TEXT,
  feature_version          TEXT,
  -- lifecycle / resolution
  status            TEXT NOT NULL,                 -- PENDING|ENTRY_TRIGGERED|ACTIVE|
                                                   -- TARGET_HIT|STOP_HIT|EXPIRED|
                                                   -- CANCELLED|COMPLETED
  resolved_at       TEXT,
  resolved_price    REAL,
  resolution_reason TEXT,
  realised_r        REAL,
  holding_bars      INTEGER
);

CREATE UNIQUE INDEX idx_pred_once   ON predictions(symbol, timeframe, created_candle_ts, source);
CREATE INDEX        idx_pred_status ON predictions(status);
CREATE INDEX        idx_pred_symcr  ON predictions(symbol, created_at);
```
- **Immutable creation fields** (audit-friendly, Vol 34): only `status`, `resolved_*`,
  `realised_r`, `holding_bars`, `updated_at` are written after creation (enforced in M2's
  store layer; documented now).
- **`context_json`** absorbs any extra context without a schema change (forward-compatible).
- **`from_row` reads by column name** (sqlite `Row`), so adding columns in future
  migrations never breaks older reads.

## 4. Migration strategy
- **Versioned, idempotent, forward-only.** `run_migrations(conn)` creates
  `schema_migrations` if absent, finds the max applied version, and applies each pending
  entry in `MIGRATIONS` (ascending) inside a transaction, recording it. Safe to call on
  **every startup**.
- **Extensible:** future tables (historical-memory aggregates, model_registry,
  gpt_history) are **new migrations** (`0002_…`, `0003_…`) — applied migrations are never
  edited. This is exactly how the DB "grows tables later without breaking compatibility."
- **Backward compatible:** the legacy `calls.db` (You-vs-AI tracker) is untouched;
  `prediction_history.db` is separate and additive. (A future migration can absorb the
  tracker into the long-term store — noted, not done now.)

## 5. Architectural concerns (raised honestly)
1. **Two SQLite files now** (`calls.db` legacy + `prediction_history.db` strategic).
   Acceptable — different lifecycles; the new one is the long-term store. Consolidation is
   a later migration, not a blocker.
2. **Raw sqlite3 vs the SQLAlchemy stub.** Using raw sqlite3 (matches `tracker.py`, no new
   deps, simplest for a modular monolith). The stub remains the optional Postgres/
   SQLAlchemy future (Vol 21); nothing imports it, so no conflict. Trade-off: we hand-write
   migrations — fine at this scale, revisit Alembic at Postgres time.
3. **Concurrency (future milestones).** The M3 monitor will write while the API reads. M1
   sets **WAL mode + `busy_timeout`** on the connection now so later concurrency is safe;
   writes stay short and single-row.
4. **Version stamping.** M1 *defines* the three version columns; they are *populated* in M3
   when records are created from live recommendations (`feature_version` = a stable hash of
   `FeatureBuilder.feature_columns`; model versions from artifact meta). M1 only models &
   persists the fields.
5. **No engine coupling.** M1 imports nothing from the Prediction/Outcome engines — it is
   pure persistence + domain models. Their tests are unaffected by construction.

## 6. Test plan (M1)
- **Enum:** the 8 states exist; `is_open()` / `is_terminal()` partition them correctly.
- **Record round-trip:** `PredictionRecord → to_row() → from_row()` is lossless, incl.
  `context_json` and all three version fields.
- **Migration runner:** fresh DB → `predictions` + `schema_migrations` created; re-running
  is a **no-op** (idempotent); `schema_migrations` records version 1; a temp-path DB is used
  (no touching real data).
- **Schema:** unique index rejects a duplicate `(symbol, timeframe, candle_ts, source)`.

## 7. Milestone gate
On approval → implement M1 (6 files + tests) in one commit (code + this plan's doc status),
run the suite, confirm Prediction/Outcome tests still pass, then **STOP for review** before
M2 (Prediction Store).
