# Volume 13 — Historical Memory

## Purpose
Store **every prediction and its real outcome, permanently** — the foundation for
learning, forward-testing, similarity, failure analysis, and the "what changed since
yesterday" experience. *"I have seen this market before."*

## Status: 🟡 Building (Sprint 2). **M1 schema + M2 Store + M3 Builder + M4 Retrieval
Engine delivered.** Satellite schema (migrations `0002`–`0005`), domain models, the **Memory
Store** (idempotent CRUD), the **Memory Builder** (enrich + aggregates + backfill), and now
the **Retrieval Engine** — composes Memory Records on read, with filtered search, keyset
pagination, aggregate reads, the similarity contract, and a GPT context bundle. REST API is
the last milestone. See `sprints/sprint-02-historical-memory-plan.md`.

### As built — M1 (schema foundation)
- **Satellite design, not a copy.** Historical Memory extends the Sprint 1 `predictions`
  table with side tables keyed on `prediction_id`; it stores only what `predictions` does
  not (reasoning, embeddings, derived aggregates). `predictions` stays immutable and
  untouched (proven by migration tests: fresh DB, populated Sprint-1 upgrade, idempotency,
  rollback safety).
- **Tables:** `memory_reasoning` (1:1 — rationale/factors/rule-check + indexable
  `confidence`); `memory_embeddings` (Similarity placeholder — packed-float32 `vector`, NULL
  until Vol 14 fills it, multiple kinds per prediction); `memory_aggregates` (derived
  rollups, keyed by dimension/bucket/model_version — rebuildable, never a source of truth).
- **Models:** `MemoryReasoning`, `MemoryEmbedding`, `MemoryAggregate` (+ `AggregateDimension`)
  in `app/memory/models.py` — persistence mapping only, **no** engine imports, **no**
  build/retrieval logic yet.
- **Independence:** `app/memory/*` imports neither the Prediction nor Outcome engine
  (asserted by test). 16 migration/schema tests; full suite green.

### As built — M2 (Memory Store)
- `app/memory/store.py` — **`MemoryStore`**: persistence only over the three satellite
  tables. Reasoning (create/upsert/update/get/exists/delete), embeddings
  (create/upsert/update/get/list/exists — stores vectors, never computes them), aggregates
  (upsert/get/list/exists/delete — writes values, never computes them).
- **Guarantees** mirror `PredictionStore`: a `threading.RLock` + shared connection
  (`check_same_thread=False`) for thread safety; every write in a transaction (commit on
  success, **rollback** on error); **idempotent upserts** keyed by natural identity
  (`prediction_id`; `(prediction_id, embedding_kind)`; `(dimension, bucket, model_version)`)
  so repeated writes never duplicate and converge to the same state.
- **Typed errors** (`app/memory/errors.py`), never silent: `MemoryNotFoundError`,
  `MemoryConflictError` (PK/unique), `MemoryForeignKeyError` (unknown prediction),
  `MemorySchemaError` (unsupported `schema_version`). Structured logging on
  create/update/delete/rollback/constraint violations — identities only, never content.
- **Writes only satellite tables** — never `predictions` (asserted by test). 27 unit tests
  incl. concurrent writes; no engine imports (asserted).

### As built — M3 (Memory Builder)
- `app/memory/builder.py` — **`MemoryBuilder`**: reads a completed prediction via
  `PredictionStore` (read-only), enriches its satellites via `MemoryStore` (write-only),
  **never** touches `predictions` or runs a model. `build(prediction_id)` (skips open/missing
  → `BuildStatus`), `backfill(limit)` (enrich all resolved-but-unbuilt, idempotent), an
  optional `on_resolved()` hook (off by default — backfill is primary), `refresh_aggregates()`.
- **Enrichment** = reshaping stored facts, not creating them: a deterministic reasoning row
  (rationale + factors + rule-check + confidence mirror; build metadata — builder version +
  provenance — rides in a reserved `_builder` key, build timestamp = row `created_at`,
  schema via `schema_version`), plus an **embedding placeholder** created only when absent so
  a future Similarity vector is never nulled. Deterministic → duplicate builds converge.
- **Aggregates** (`app/memory/aggregates.py`, pure functions): win rate, avg R, expectancy,
  profit factor, total R, max drawdown, avg holding — by overall/symbol/sector/regime/
  timeframe/confidence-bucket, **and** split per prediction-model version (so a model swap
  never blends). Maintained by **recompute-from-source** (derived + rebuildable, always
  correct) rather than fragile running counters.
- **Resilience:** backfill continues past a single enrichment failure (logged + counted),
  never corrupting prior memory; every store write is transactional. Structured logging of
  build start/complete/skip/aggregate-refresh/backfill-summary — identifiers only, no
  content. 20 integration tests (build, skip, aggregate correctness vs known data, backfill
  idempotency, rollback resilience, concurrency, `predictions` unchanged, no engine imports).

### As built — M4 (Retrieval Engine)
- `app/memory/retrieval.py` — **`RetrievalEngine`**: the **read-only** layer. Composes a
  `MemoryRecord` **on read** from the prediction (via `PredictionStore`) + its satellites
  (via `MemoryStore`); a missing satellite yields `null`/defaults, never an error. Performs
  no writes and issues no direct SQL; imports neither engine (asserted).
- **Search** (`MemoryFilter`): by symbol, timeframe, regime, sector, prediction-model /
  outcome-model / feature version, confidence range, outcome status (WIN/LOSS aliases), and
  date range — all AND-composed. **Keyset pagination** ordered `(created_at, prediction_id)`
  desc — deterministic and reproducible; malformed filters/cursor/limit raise
  `MemoryQueryError`.
- **Aggregates:** read-only pass-through to `MemoryStore` (never computes here).
- **Similarity contract:** `similar()` returns an explicit *"Similarity Engine unavailable"*
  with **no** fabricated scores (validates the prediction exists first) — the algorithm is
  Vol 14.
- **GPT context bundle:** bounded, deterministic — top-k matching records + the relevant
  aggregate + **sample size** + an honest small-sample note, so the assistant can't
  over-claim. 24 integration tests (composition, missing satellites, every filter,
  pagination completeness, aggregate reads, similarity-unavailable, GPT bundle, empty + large
  datasets, `predictions` unchanged / no writes).

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
