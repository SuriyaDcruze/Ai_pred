# Sprint 1 — Forward Testing Engine · Implementation Plan (awaiting approval)

> Per the mandated process: **plan → files → DB → APIs → wait for approval → implement.**
> This document is the plan. No engine code is written until approved.

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
| `engine.py` | `ForwardTestingEngine` — `record_from_recommendation()`, `monitor_once()`, `stats()` |
| `scheduler.py` | the async monitor loop (started in lifespan) |

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

New SQLite store `data/forward.db` (kept separate from the You-vs-AI `calls.db`; migrates
to Postgres per Vol 21 later). One table:

```sql
CREATE TABLE predictions (
  prediction_id   TEXT PRIMARY KEY,
  created_at      TEXT, created_candle_ts INTEGER,
  symbol TEXT, exchange TEXT, timeframe TEXT, source TEXT,
  current_price   REAL,
  direction TEXT, direction_prob REAL, outcome_prob REAL, decision_score REAL,
  entry REAL, stop REAL, target1 REAL, target2 REAL,
  recommendation  TEXT,
  model_version TEXT, feature_version TEXT,           -- stamped from artifact meta
  status TEXT,                                        -- the 8-state enum
  resolved_at TEXT, resolved_price REAL, resolution_reason TEXT,
  realised_r REAL, holding_bars INTEGER,
  market_context_json TEXT                            -- market state, sector, similarity
);
CREATE UNIQUE INDEX idx_once ON predictions(symbol, timeframe, created_candle_ts, source);
```
- **Immutable:** creation fields never edited; only status/resolution columns are written
  on resolve (audit-friendly, Vol 34).
- **Version stamps:** `model_version` = sklearn artifact meta (`model_name`+train range);
  `feature_version` = hash of `FeatureBuilder.feature_columns`.

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
