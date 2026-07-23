# Sprint 1 Report — Forward Testing Engine

- **Sprint:** 1 · Forward Testing Engine
- **Status:** ✅ **COMPLETE**
- **Recommended release tag:** `v0.1.0-forward-testing`
- **Version:** `app/__init__.py` → `0.1.0`
- **Repo:** `SuriyaDcruze/Ai_pred` · branch `main`

> Detailed plan and per-milestone status: [../architecture/sprints/sprint-01-forward-testing-plan.md](../architecture/sprints/sprint-01-forward-testing-plan.md).
> API reference: [../api/forward-testing.md](../api/forward-testing.md).
> Decisions: [../architecture/adr/](../architecture/adr/). Release notes: [../releases/v0.1.0-forward-testing.md](../releases/v0.1.0-forward-testing.md).

---

## 1. Goals
Turn the **backtest-only** outcome-model edge into the beginnings of a **real, logged, live
track record** — the one thing standing between "verified in backtest" and any honest claim
of live viability. Concretely:
- Persist every actionable recommendation with full context, immutably and auditably.
- Resolve each against real future price (target / stop / expiry), restart-safely.
- Expose it over a clean REST API and an honest dashboard that never over-claims.
- Do all of this **without touching** the Prediction or Outcome engines (ADR 0002/0003).

## 2. Completed milestones
| M | Scope | Tests | Commit |
|---|---|---|---|
| M1 | DB foundation — schema, `PredictionRecord`/`PredictionStatus`, versioned migrations | 33 | `f959619` |
| M2 | `PredictionStore` — CRUD, dedupe, status/resolution, queries, statistics, restart-safe | 36 | `b1fa57c` |
| M3 | Engine — resolver (stop-first pessimistic + expiry), state machine, background monitor | 23 | `0d2b65c` |
| M4 | REST API — `/forward/*` (record, get, active, completed, stats, summary) | 19 | `d7ac02a` |
| M5 | Dashboard — overview, live-vs-backtest, breakdown, active/completed, timeline | 17 | `bf85d0b` |
| M6 | Documentation & sprint closure | — | *(this milestone)* |

**Forward-testing tests: 128.** Every milestone was plan-gated: plan → approve → implement
one milestone → review → next.

## 3. Architecture decisions
Captured as ADRs ([../architecture/adr/](../architecture/adr/)):
- **0001** Modular monolith (not microservices).
- **0002 / 0003** Prediction Engine and Outcome Engine are immutable — consumed, never
  imported or modified.
- **0004** Forward testing before production / real money.
- **0005** A single `prediction_history.db`, evolved by append-only migrations.
- **0006** REST API separated from engine logic; aggregation server-side, dashboard
  presentation-only.

Design highlights: rich context + three independent version stamps captured at creation;
immutable creation fields (only lifecycle columns mutate); idempotent create (unique index)
and idempotent resolve (terminal-state guard); restart safety (open set re-read from the DB,
never held only in memory); WAL + busy-timeout + an in-process lock for the monitor's worker
thread.

## 4. Lessons learned
- **The tests earn their keep.** M3's concurrency bug (`SQLite objects can only be used in
  the thread that created them`) was caught by a start/stop monitor test, not in production —
  fixed with `check_same_thread=False` + a store `RLock`.
- **Honesty must be enforced in code, not just intended.** `/forward/summary` and the
  dashboard return `no_data` / `building_sample` / `inconclusive` / `significant` with a
  sample size, so no one can accidentally present a few trades as an edge.
- **A spec can over-reach the current API.** M5's breakdown and live-vs-backtest needed data
  M4 didn't expose. The honest resolution was a small **server-side** API extension (reusing
  the store), not aggregation in the browser — keeping the dashboard presentation-only.
- **Process discipline works.** Milestone gating caught an early scope slip (starting code
  before the plan was approved) and kept the engines provably untouched throughout.

## 5. Known limitations
- **Backtest-only edge, still.** Forward Testing is the *instrument*; it has not yet
  accumulated a live sample. Every performance number remains backtest until N is large
  enough (target 50–100+ resolved live trades).
- **No auto-record path yet.** Predictions are recorded explicitly via `POST
  /forward/prediction`; the pipeline does not yet auto-record TAKE recommendations.
- **Background monitor not wired into the app lifespan.** `ForwardTestingMonitor` exists and
  is tested, but is not started by the running server yet (no live candle-fetcher wiring).
- **"Current R" for open trades is unrealised** — shown as `—/open`; no live mark-to-market.
- **SQLite single-writer** (mitigated by WAL + lock); Postgres is the future path (Vol 21).
- **Backtest baseline is a single configured constant** (crypto WF 59.6%); it is a reference
  for honesty, not a per-market/per-model comparison.

## 6. Technical debt
- Wire the monitor into the FastAPI lifespan with a real provider candle-fetcher + config
  gate; add an auto-record hook from the analysis pipeline.
- A market-aware backtest baseline (NSE vs crypto) rather than one constant.
- Coverage tooling is not configured in CI (see Testing); add `pytest-cov` gates.
- The dashboard has no auto-refresh/streaming; it fetches on load and on manual refresh.
- Two aggregation paths exist (`PredictionStore.statistics` for `/stats`;
  `forward_analytics.aggregate` for breakdown/summary) — acceptable, but a future unify.

## 7. Future work
- **Accumulate the live sample** — the whole point; weeks-to-months of resolved trades.
- Auto-record + live monitor (above) to make the record self-populating.
- **Sprint 2 — Historical Memory Engine** (designed, then deferred at the user's request to
  finish Sprint 1 first): satellite tables over `predictions` (embeddings, news, reasoning,
  aggregates) powering Similarity, Learning, GPT, Decision Intelligence.
- Live-vs-backtest drift alerts; per-sector/regime significance once samples allow.

---

## 8. Testing
- **Total tests:** **361 passed, 0 failed** (full suite, ~3–8 min depending on the training
  smoke test). Forward-testing-specific: **128** (M1 33 · M2 36 · M3 23 · M4 19 · M5 17).
- **Coverage:** `pytest-cov` is **not installed/configured**, so no numeric coverage figure
  is claimed here (stating one would be dishonest). Coverage is instead described by intent:
  the forward-testing packages (`app/forward_testing/*`, `app/api/forward.py`,
  `app/api/forward_analytics.py`) are exercised across unit, integration, and API tests;
  adding a `pytest-cov` gate is tracked as technical debt.
- **Integration tests:** end-to-end over a temporary DB — record → resolve → query
  (`active`/`completed`/`stats`/`summary`/`breakdown`); restart-safety (reload open and
  resume); monitor start/stop across threads; live-vs-backtest status transitions; empty and
  error states; static delivery of `forward.html` with all six sections.
- **Isolation guarantees (asserted by tests):** the forward-testing code and API import
  **nothing** from `app.ai.sklearn_model` / `app.ai.outcome_model` (AST checks); all tests
  use **temporary databases** — production `data/prediction_history.db` is never touched.
- **Manual verification:** `git status app/ai/ app/forward_testing/` clean after M4/M5 (core
  + engines untouched); app imports with 6 (M4) then 7 (M5, incl. `/breakdown`) forward
  routes registered; dashboard served at `/dashboard/forward.html` and linked from the main
  dashboard; `/forward/summary` shows `no_data` on an empty store and a `building_sample`
  live-vs-backtest read after seeding.
- **Performance notes:** aggregate reads are cheap (statistics over resolved records; WAL
  keeps readers off the writer). No large-scale (10k–100k) load test was run yet — a seeded
  performance test is future work (relevant once Historical Memory and larger histories
  arrive). The full suite's runtime is dominated by an unrelated 1-epoch model-training
  smoke test, not by forward-testing tests (128 run in ~7.5s combined).
