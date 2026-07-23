# Sprint 1 — Forward Testing Engine · Implementation Plan & Status

> Per the mandated process: **plan → files → DB → APIs → wait for approval → implement**,
> milestone by milestone with a review gate after each.

## 📊 Milestone status

| Milestone | Scope | Status | Tests | Commit |
|-----------|-------|--------|-------|--------|
| **M1** | DB foundation: schema, `PredictionRecord`/`PredictionStatus`, versioned migrations | ✅ **done — approved** | 33 | `f959619` |
| **M2** | `PredictionStore`: CRUD, duplicate protection, status/resolution updates, active/completed queries, statistics, restart-safe | ✅ **done — approved** | 36 | `b1fa57c` |
| **M3** | Forward Testing Engine: resolver, state machine, background monitor, restart-safe | ✅ **done — approved** | 23 | `0d2b65c` |
| **M4** | REST API: `/forward/*` endpoints (POST prediction, get by id, active, completed, stats, summary) | ✅ **done — approved** | 19 | `d7ac02a` |
| **M5** | Dashboard: overview, live-vs-backtest, breakdown, active/completed, timeline | ✅ **done — approved** | 17 | `bf85d0b` |
| **M6** | Documentation & closure: ADRs, API reference, sprint report, results, release notes | ✅ **done** | — | (this) |

> ## 🏁 Sprint 1 status: **COMPLETE** · recommended tag `v0.1.0-forward-testing`
> All six milestones delivered, plan-gated, with the Prediction/Outcome engines and the
> M1–M3 core proven untouched throughout. Forward-testing tests: **128**; full suite: **361
> passed**. Closure docs: [sprint report](../../sprints/sprint-01-report.md) ·
> [API reference](../../api/forward-testing.md) · [ADRs](../adr/) ·
> [release notes](../../releases/v0.1.0-forward-testing.md).

- **Full suite after M3:** 325 passed, 0 failed. Prediction/Outcome engines untouched &
  verified unaffected. Forward-testing code imports **nothing** from the engines.
  M3 note: added `check_same_thread=False` + a store lock so the background monitor's
  worker thread can share the connection safely (a real concurrency bug the tests caught).
- **Persistence:** the single store is **`data/prediction_history.db`** (created lazily by
  the first migration run) — there is no separate `forward.db`.
- **M4 REST API (as built):** `app/api/forward.py` (`APIRouter`, `/forward/*`) mounted in
  `app/api/main.py`; store + engine created in the lifespan on `app.state`. Six endpoints
  (POST prediction, GET by id, active, completed, stats, summary), pydantic request
  validation (`ForwardPredictionRequest`), `404`/`409`/`422` error handling, honest
  sample-size confidence in `/forward/summary`. 19 API tests, all via a temporary DB — no
  model logic and **no engine imports** (asserted by a test). Prediction/Outcome engines
  untouched (`git status app/ai/` clean).
- **M5 Dashboard (as built):** `app/dashboard/static/forward.html` — a **presentation-only**
  page (served by the existing StaticFiles mount at `/dashboard/forward.html`, linked from
  the main dashboard) that consumes `/forward/*` and renders six sections: Overview,
  Live-vs-Backtest, Performance Breakdown, Active, Completed, Timeline. No business logic
  in the browser and no direct DB access. Sections 2–3 needed data the M4 API didn't
  expose, so the **Forward REST API was extended additively, server-side**: `GET
  /forward/breakdown?by=market|sector|timeframe|confidence|regime` (grouped aggregates) and
  new `backtest` / `live_vs_backtest` / `expectancy` fields on `/forward/summary`.
  Aggregation lives in `app/api/forward_analytics.py` (pure functions, no engine imports).
  The backtest baseline is a **configured constant** sourced from the documented
  outcome-model walk-forward result (59.6% win rate / +0.285 avg R / PF 1.63,
  `reports/outcome_model_summary.md`) — overridable via `AEGIS_FORWARD_BACKTEST_*`, and a
  negative win rate declares "no baseline configured". `live_vs_backtest` reports an honest
  status (`no_data` / `building_sample` / `inconclusive` / `statistically_significant`)
  using a 95% CI, never over-claiming. 17 dashboard tests (temporary DB); M1–M3 core and
  the Prediction/Outcome/Risk engines untouched.
- **Push:** M1–M3 are pushed to `SuriyaDcruze/Ai_pred` (repo-local credential override
  selects the `SuriyaDcruze` account; global git/gh config untouched).

## Guardrails (verified against the repo)
- **Do NOT touch** the Prediction Engine (`app/ai/sklearn_model.py`) or Outcome Engine
  (`app/ai/outcome_model.py`). Forward Testing **consumes** their outputs via
  `AnalysisService` — read-only.
- **Modular monolith** — new code lives in a new package `app/forward_testing/`; no
  microservices, no new runtime infra beyond an in-process scheduled task.
- **Reuse, don't duplicate:** the barrier-resolution logic (`tracking/tracker.py::
  resolve_call`, stop-first pessimistic) and the background-loop pattern
  (`api/main.py::_autolog_loop`) already exist — the FT engine reuses both.
- **Existing APIs unchanged** — all new endpoints under `/forward/*`.

## 1. Implementation plan (approach)

A dedicated **Forward Testing Engine** that (a) records a rich Prediction Record whenever
the pipeline emits a TAKE recommendation for a watched symbol, (b) runs an idempotent,
restart-safe background monitor that resolves open predictions against real future price,
and (c) exposes stats + a dashboard. It is the *discipline layer* over the existing
record/resolve mechanism.

### Prediction lifecycle (state machine)
```
        create (TAKE recommendation)
                 │
                 ▼
            PENDING ──(entry is market price)──▶ ACTIVE
                 │  (limit entry: wait)              │
                 ▼                                   ▼
          ENTRY_TRIGGERED ──▶ ACTIVE      ┌── TARGET_HIT ─┐
                 │                         ├── STOP_HIT ───┤──▶ COMPLETED
              CANCELLED                    └── EXPIRED ────┘
```
- Default entry = current price → PENDING resolves to ACTIVE immediately (market model).
- EXPIRED when `now > created + max_hold_bars` without target/stop.
- Every terminal state → COMPLETED with realised R + holding period, then written to
  Historical Memory.

### Idempotency & restart safety (Vol 33)
- **Idempotent create:** unique index `(symbol, timeframe, created_candle_ts, source)` —
  a second create for the same candle is a no-op (mirrors the existing autolog dedupe).
- **Idempotent resolve:** resolution only transitions OPEN→terminal; re-running on a
  resolved record is a no-op (status guard).
- **Restart safe:** on startup the monitor loads all OPEN predictions from the store and
  resumes; no state is held only in memory.

### Background monitor (reuses the `_autolog_loop` pattern)
An asyncio task under the FastAPI lifespan (same pattern already in `api/main.py`):
```
loop every N seconds:
  for each OPEN prediction:
     fetch latest candles (provider, with fallback)
     walk candles since entry → target? stop? expired?   ← reuse resolve_call logic
     if terminal: update status + realised_R + holding, write to Historical Memory
  sleep (tf-aware; daily NSE polled less often than 1h crypto)
```
Config-gated (off by default until watchlist configured); cancels cleanly on shutdown.

## 2. Files that will change / be created

**New (`app/forward_testing/`):**
| File | Responsibility |
|---|---|
| `__init__.py` | package |
| `models.py` | `PredictionRecord` dataclass + `PredictionStatus` enum (the 8 states) |
| `store.py` | `PredictionStore` (SQLite) — create/get/list/update, stats; unique-index dedupe |
| `resolver.py` | resolve one OPEN record against candles (reuses `tracker.resolve_call` barrier logic; adds EXPIRED + entry-trigger) |
| `engine.py` | `ForwardTestingEngine` — `record()`, `monitor_once()` |
| `monitor.py` | the async monitor loop (`ForwardTestingMonitor`; wired into lifespan in a later milestone) |

**New tests:** `tests/test_forward_testing.py` (unit + integration + e2e).

**Changed (additive only — no logic change to prediction/outcome):**
| File | Change |
|---|---|
| `app/api/main.py` | add `/forward/*` endpoints; start/stop the FT monitor task in `lifespan` (alongside the existing autolog task) |
| `app/service.py` | small helper `build_recommendation_record()` that assembles a full record from existing intelligence/screener/risk outputs (reuse, no new model logic) |
| `app/config.py` | FT settings: `forward_testing_enabled`, `forward_watchlist`, `forward_poll_secs`, `forward_max_hold_bars` |
| `app/dashboard/static/index.html` | a "📡 Forward Testing" panel (active, completed, win rate, PF, avg R, max DD, avg holding, open risk) |

**Docs (same commit as code, per the docs-before-push rule):**
- `docs/architecture/18-forward-testing.md` → status 🔴→🟡/🟢, note the implementation.
- Author lightweight **Vol 33 — Background Jobs** (33-background-jobs.md) documenting the
  monitor pattern actually used.
- `docs/architecture/20-api-architecture.md` + a `/forward/*` contract stub → **Vol 31**
  start.
- `docs/RESULTS.md` → "Forward Testing engine built; live record accumulating."
- Testing docs (Vol 26) → the new test suite.

## 3. Database changes

The permanent long-term store **`data/prediction_history.db`** (kept separate from the
legacy You-vs-AI `calls.db`; migrates to Postgres per Vol 21 later). It is the single
store for Forward Testing, Historical Memory, Learning, Similarity, and the Model
Registry (future) — new tables arrive as new migrations. As-built schema
(migration `0001_create_predictions`, see `app/database/migrations.py`):

```sql
CREATE TABLE predictions (
  prediction_id   TEXT PRIMARY KEY,
  created_at TEXT, updated_at TEXT, created_candle_ts INTEGER,
  symbol TEXT, exchange TEXT, timeframe TEXT, source TEXT,
  current_price REAL,
  direction TEXT, direction_prob REAL, outcome_prob REAL, decision_score REAL,
  recommendation TEXT,
  entry REAL, stop REAL, target1 REAL, target2 REAL,
  -- rich context (explainability + future learning), individually queryable:
  market_regime TEXT, market_phase TEXT, sector TEXT, session TEXT,
  volatility_bucket TEXT, similarity_score REAL, context_json TEXT,
  -- three independent version stamps:
  prediction_model_version TEXT, outcome_model_version TEXT, feature_version TEXT,
  status TEXT,                                        -- the 8-state enum
  resolved_at TEXT, resolved_price REAL, resolution_reason TEXT,
  realised_r REAL, holding_bars INTEGER
);
CREATE UNIQUE INDEX idx_pred_once ON predictions(symbol, timeframe, created_candle_ts, source);
CREATE INDEX idx_pred_status ON predictions(status);
CREATE INDEX idx_pred_symbol_created ON predictions(symbol, created_at);
```
- **Immutable:** creation fields never edited; only status/resolution columns change on
  resolve — enforced by the store's lifecycle-only writes (audit-friendly, Vol 34).
- **Version stamps:** three independent columns (`prediction_model_version`,
  `outcome_model_version`, `feature_version`) — a model swap never invalidates old records.

## 4. New APIs (`/forward/*` — existing APIs untouched)

| Method · Path | Purpose | Returns |
|---|---|---|
| `POST /forward/prediction` | manually record a prediction (auto path also exists) | `{prediction_id, status}` |
| `GET /forward/prediction/{id}` | one record | full `PredictionRecord` |
| `GET /forward/active` | OPEN predictions (PENDING/ACTIVE/ENTRY_TRIGGERED) | list |
| `GET /forward/completed?limit=` | resolved predictions | list |
| `GET /forward/stats` | aggregate: win rate, profit factor, avg R, max drawdown, avg holding, open risk, n | object |
| `GET /forward/summary` | stats + live-vs-backtest note + honest sample-size caveat | object |

All responses are structured (feed the LLM & dashboard). Disclaimers included ("backtest
edge; live sample building; not proven until N≥50–100").

## 5. Testing plan

- **Unit:** state-machine transitions; resolver (target-first, stop-first, expiry,
  same-candle pessimism); idempotent create (dupe candle = no-op); idempotent resolve;
  stats math (win rate, PF, avg R, max DD, open risk).
- **Integration:** `record_from_recommendation()` → store → `monitor_once()` on synthetic
  future candles → COMPLETED with correct R; restart-safety (reload OPEN and resume).
- **E2E:** `/forward/prediction` → `/forward/active` → resolve → `/forward/completed` +
  `/forward/stats`; assert the Prediction/Outcome engines are **unaffected** (their tests
  still pass; no import cycle; FT is read-only over them).

## 6. What this sprint does NOT do
- No changes to Prediction/Outcome models. No live/real trading. No Postgres yet (SQLite).
- No microservices, no message bus, no new heavy infra (in-process asyncio monitor only).

---

## Deliverables checklist (this turn = plan only)
1. ✅ Implementation plan (above). 2. ✅ Files identified. 3. ✅ DB changes. 4. ✅ New APIs.
5. ⏳ **Awaiting approval before implementation.**
