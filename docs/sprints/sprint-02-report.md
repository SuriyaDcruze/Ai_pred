# Sprint 2 Report — Historical Memory Engine

- **Sprint:** 2 · Historical Memory Engine
- **Status:** ✅ **COMPLETE**
- **Recommended release tag:** `v0.2.0-historical-memory`
- **Version:** `app/__init__.py` → `0.2.0`
- **Repo:** `SuriyaDcruze/Ai_pred` · branch `main`

> Plan & per-milestone status: [../architecture/sprints/sprint-02-historical-memory-plan.md](../architecture/sprints/sprint-02-historical-memory-plan.md).
> API reference: [../api/historical-memory.md](../api/historical-memory.md).
> Decisions: [../architecture/adr/](../architecture/adr/) (0007–0011). Release notes:
> [../releases/v0.2.0-historical-memory.md](../releases/v0.2.0-historical-memory.md).

---

## 1. Objectives
Build Aegis' **permanent knowledge layer** over the Sprint 1 `predictions` fact table: turn
each completed prediction into an enriched, retrievable **Memory Record**, and serve those
records (individually, filtered, aggregated, and — via a fixed contract — by similarity) to
future consumers, **without** modifying Sprint 1 or the Prediction/Outcome engines, and
reusing the one `prediction_history.db`.

## 2. Completed milestones
| M | Scope | Tests | Commit |
|---|---|---|---|
| M1 | Database Extension — satellite schema (migrations `0002`–`0005`) + models | 16 | `d7e22ef` |
| M2 | Memory Store — thread-safe, idempotent CRUD over satellites | 27 | `bad58f0` |
| M3 | Memory Builder — enrich + aggregates + backfill + optional hook | 20 | `b59d785` |
| M4 | Retrieval Engine — compose, filter, keyset-paginate, similarity contract, GPT bundle | 24 | `2f30d87` |
| M5 | REST API — `/memory/*` (9 endpoints), thin transport | 23 | `3c64181` |
| M6 | Documentation & freeze | — | *(this milestone)* |

**Historical Memory tests: 110.** Every milestone was plan-gated (plan → approve → implement
→ review → next), each proving Sprint 1 + the engines untouched.

## 3. Architecture decisions (ADRs 0007–0011)
- **0007** Satellite-table architecture — store only what `predictions` doesn't; keyed on
  `prediction_id`; additive-only.
- **0008** Memory Record composed on read — one source of truth, no dual-write drift.
- **0009** Retrieval reads predictions only via `PredictionStore` — Sprint 1 keeps ownership;
  deterministic keyset pagination.
- **0010** `/memory/*` API is thin transport — no business logic, no direct DB, no engine
  imports.
- **0011** Similarity: contract now, algorithm later — explicit "unavailable", never a fake
  score.

## 4. Platform layer status (which layers are complete)
```
  Prediction Engine  🟢 built (Sprint 0/1)   ─┐
  Outcome Engine     🟢 built                 ─┼─►  (immutable; never imported by HME)
  Risk Engine        🟢 built                 ─┘
        │
        ▼
  FORWARD TESTING     🟢 COMPLETE (Sprint 1, v0.1.0) — record + resolve + API + dashboard
        │
        ▼
  HISTORICAL MEMORY   🟢 COMPLETE (Sprint 2, v0.2.0) — store + builder + retrieval + API
        │                                            (embeddings: storage only; NULL vectors)
        ▼
  DECISION INTELLIGENCE  🔴 not built — will consume Memory Records + aggregates
        ▼
  SIMILARITY ENGINE      🔴 not built (Vol 14) — will fill memory_embeddings; contract ready
        ▼
  LEARNING ENGINE        🟡 partial (Vol 15) — will train on the labelled history HME serves
        ▼
  GPT ASSISTANT          🟡 basic (Vol 07) — /memory/context bundle ready to ground it
```

## 5. Testing summary
- **Total suite: 471 passed, 0 failed.** Historical Memory: **110** (M1 16 · M2 27 · M3 20 ·
  M4 24 · M5 23).
- **Coverage:** `pytest-cov` still not configured (tracked debt from Sprint 1); coverage is
  described by intent — the `app/memory/*` package and `app/api/memory.py` are exercised across
  migration, unit, integration, and API tests.
- **Guarantees asserted by tests:** `app/memory/*` and `app/api/memory.py` import **neither**
  engine (AST checks); Historical Memory issues **no writes** to `predictions` (snapshot
  equality); migrations keep `predictions` byte-for-byte unchanged on a populated Sprint-1 DB;
  all tests use temporary databases.
- **Manual verification:** app boots with 9 `/memory/*` + 7 `/forward/*` routes; OpenAPI
  generates; `git status app/ai app/forward_testing app/database` clean after each milestone.

## 6. Performance summary
- **Growth (estimates):** satellites add ~0.5 KB/record (reasoning+aggregates); embeddings
  ~1.5 KB per 384-dim vector — even ~1000 predictions/day is ~1 GB/year, within SQLite.
- **Reads:** aggregate lookups are O(1) from `memory_aggregates`; record composition is a
  small per-record satellite fan-out (only the page is composed). Search filters in-app over
  `PredictionStore` reads — instant at current volumes (the 120-record pagination test runs in
  milliseconds).
- **No large-scale (10k–100k) load test run yet** — deferred until the live sample and/or
  Similarity make it meaningful (future work).

## 7. Known limitations
- **Memory is only as full as the live sample** — Forward Testing's sample is still
  accumulating (Sprint 1 tech debt: auto-record + monitor wiring), so Historical Memory is
  near-empty in production until then. All retrieval/aggregate paths handle the empty state.
- **Embeddings are storage-only** — `memory_embeddings` holds NULL vectors; nothing computes
  them (Vol 14). `/memory/similar` is deliberately "unavailable".
- **Retrieval filters in-app**, not via the M1 `predictions` indexes (ADR 0009) — correct and
  fast now; those indexes are forward-investment for a query API or Postgres.
- **Aggregates recompute from source** (not running counters) — simple and always correct;
  cheap at our scale, O(N) per refresh.
- **`memory_news` (T4) deferred** — optional satellite, not built.
- **No memory dashboard UI** — out of Sprint 2 scope.
- **Metadata rides in the reasoning `_builder` key**, not a dedicated column (no M3 schema
  change) — a `metadata_json` column is a possible future additive migration.
- **SQLite single-writer** (WAL + lock mitigate) — Postgres remains the exit (Vol 21).

## 8. Future roadmap
- **Feed memory:** wire Forward Testing's auto-record + background monitor so memory populates
  live (Sprint 1 tech debt).
- **Vol 14 — Similarity Engine:** compute embeddings into `memory_embeddings`; implement the
  algorithm behind the existing `/memory/similar` contract (filter-then-brute-force → pgvector
  at scale).
- **Vol 15 — Learning Engine:** train/evaluate on the labelled history HME serves (model
  changes go through the model process, ADR 0002/0003).
- **Decision Intelligence + Portfolio Intelligence + Performance Analytics:** consume Memory
  Records + aggregates.
- **Platform:** `pytest-cov` gates; a memory dashboard; the Postgres migration (Vol 21) with
  pgvector.

---

## 9. Sprint 2 freeze summary
- **Milestones completed:** 6 / 6 (M1–M6).
- **Total modules added:** 7 — `app/memory/{models,errors,store,aggregates,builder,retrieval}.py`
  + `app/api/memory.py` (plus package `__init__`).
- **Total migrations added:** 4 (`0002`–`0005`; `0006` news deferred).
- **Total API endpoints added:** 9 (`/memory/*`).
- **Total tests added:** 110 (suite now **471**, 0 failed).
- **Database changes:** 3 new satellite tables (`memory_reasoning`, `memory_embeddings`,
  `memory_aggregates`) + 4 additive `predictions` indexes; `predictions` schema & data
  **unchanged**; one database (`prediction_history.db`), no new DB.
- **Sprint 1 & engines:** provably untouched (import-guard + no-write + migration tests).
- **Outstanding future work:** feed memory live; Similarity (Vol 14); Learning (Vol 15);
  Decision/Portfolio Intelligence; coverage gates; Postgres/pgvector.

**Definition of Done — met:** every completed prediction composes into a full Memory Record
(NULL embeddings until Similarity); all retrieval paths work with pagination + honest sample
sizes; aggregates answer "how have setups like this performed?" per dimension and model
version; backfill enriches pre-existing predictions; `predictions` + engines provably
unchanged; downstream consumers have a stable, documented contract.
