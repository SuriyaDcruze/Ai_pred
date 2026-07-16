# Volume 19 — Backend Architecture

## Purpose
Define how the server is structured: the FastAPI app, the service/orchestration layer,
async I/O, background tasks, and the module boundaries that keep it a *modular* monolith.

## Status: 🟡 Built (monolith) — `app/api/main.py`, `app/service.py`, `app/config.py`

## Layers
```
API layer        app/api/main.py        — routes, request/response, WS, background loop
Service layer    app/service.py         — AnalysisService orchestrates engines
Engine layer     app/ai, intelligence,  — Prediction, Outcome, Intelligence, Sector,
                 sector, risk, screener     Risk, Screener (single responsibilities)
Feature layer    app/features, indicators — shared FeatureBuilder (train == inference)
Data layer       app/stream, app/data    — providers (Yahoo/Binance), schemas
Persistence      app/tracking (SQLite)   — call store; → Postgres (Vol 21)
```

## Responsibilities & principles
- **Thin routes, fat service:** endpoints validate + delegate to `AnalysisService`; no
  business logic in routes.
- **Async-first:** FastAPI async handlers; blocking work (model fit in screener/
  intelligence) offloaded via `anyio.to_thread` so the event loop isn't blocked.
- **Background tasks:** server-side loops (auto-log, future forward-testing scheduler) via
  `asyncio.create_task` under the FastAPI lifespan.
- **Config:** `pydantic-settings` (`app/config.py`) — thresholds, risk %, model paths, env.
- **Lazy model loading:** artifacts loaded once, cached on the service; graceful fallback
  chain (sklearn → deep → heuristic).

## Concurrency & performance
- Model inference is CPU-bound and fast (logistic/HistGB). Screener parallelism is bounded
  by per-stock fetch+fit; cache sector/index data (15-min TTL) to avoid refetch storms.

## Failure handling
- Provider errors → fallback (mirror/poll/cache). Engine errors in the context layer →
  degrade that section, never the core signal. All logged (`app/utils/logging.py`,
  RichHandler only on a TTY).

## Testing
- Service + endpoint tests; the engine unit tests underneath. Target: contract tests per
  route.

## Prediction-Model integration
- `AnalysisService` is the single place engines are wired; the LLM route (`/chat`) is
  given only read access to engine outputs.

## Future
- Extract heavy/stateless engines to workers if needed (Vol 04 §7); a task queue
  (e.g. RQ/Celery) for forward-testing/retrain scheduling; caching layer (Redis).
