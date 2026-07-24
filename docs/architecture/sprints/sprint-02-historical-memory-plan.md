# Sprint 2 — Historical Memory Engine · Architecture & Implementation Plan

> **Planning only. No code, no SQL, no endpoints are written in this document.**
> Process (identical to Sprint 1): **Architecture → Sprint Plan → Milestones → Review →
> Approval → Implementation.** Implementation begins only after this plan is approved, and
> then one milestone at a time with a review gate after each.
>
> **Status:** 🟡 Design — awaiting approval. Nothing implemented.

**Related:** Vol 13 (Historical Memory), Vol 14 (Similarity), Vol 15 (Learning), Vol 21
(Database Design), Vol 18 (Forward Testing — the upstream producer),
[ADRs](../adr/) 0001/0002/0003/0005/0006, and the
[Sprint 1 report](../../sprints/sprint-01-report.md).

---

## 0. Ground truth — what Sprint 1 actually left us

Design starts from what exists, not from what we wish existed.

| Sprint 1 component | State | Relevance to Sprint 2 |
|---|---|---|
| `data/prediction_history.db` | ✅ built | the one database Sprint 2 extends |
| `predictions` table (migration `0001`) | ✅ built | **the canonical fact table** — Historical Memory reads it |
| Versioned, append-only, idempotent migration runner | ✅ built | the only mechanism Sprint 2 uses to add schema |
| `PredictionStore` | ✅ built | read API for predictions (`get`, `list_completed`, `list_active`, `statistics`) |
| Forward Testing Engine + Resolver | ✅ built | produces the terminal states that trigger memory |
| `ForwardTestingMonitor` | ✅ built, **not wired into the app** | matters: the resolution *hook* cannot be assumed |
| `/forward/*` REST API + dashboard | ✅ built | the pattern Sprint 2's API and (later) UI follow |
| **Live track record** | ⚠️ **engine built, sample not yet accumulated** | Historical Memory will initially be **nearly empty** — design and tests must not assume data |

**Two facts that shape the entire design:**

1. **The `predictions` table already stores most of the canonical Historical Memory Record.**
   Of the 22 field-groups requested for the record, **17 already exist** in `predictions`
   (prediction, outcome, trade result, realised R, holding period, regime, phase, sector,
   session, timeframe, volatility, confidence, all three version stamps, decision score,
   recommendation, risk information, context). Only **reasoning, metadata, embeddings** (and
   optionally a news snapshot) are genuinely new. Duplicating the other 17 into a second
   table would create two sources of truth and a dual-write consistency problem for zero
   benefit. **Decision: compose, don't copy** (§4.1).

2. **Historical Memory has no data of its own to protect yet.** Because the live sample is
   still accumulating, Sprint 2 can be built and validated on synthetic/seeded data, and a
   backfill path is mandatory (memory must be buildable for predictions that resolved
   *before* the engine existed).

---

## 1. Executive Summary

The **Historical Memory Engine (HME)** is Aegis' permanent knowledge layer. It turns each
completed prediction into a structured, enriched, retrievable **Memory Record**, and serves
those records — individually, filtered, aggregated, and (later) by similarity — to every
downstream consumer: Decision Intelligence, Similarity Search, the GPT Assistant, the
Learning Engine, the Model Registry, Portfolio Intelligence, and Performance Analytics.

**What it is:** a *read + enrich + index + retrieve* layer over the existing `predictions`
table, plus a small set of **satellite tables** (reasoning, embeddings, aggregates, and an
optional news snapshot) in the **same** `prediction_history.db`, added by **new append-only
migrations**.

**What it is not:** not a second database, not a copy of `predictions`, not a prediction
engine. It **never** performs inference, never retrains, never modifies a prediction's
results. *Historical Memory stores facts; it never creates them.*

**Why this shape:** it is the only design that satisfies all four hard constraints
simultaneously — reuse one database, extend only via migrations, never modify Forward
Testing's fields, and stay completely independent of the Prediction/Outcome engines.

**Scope:** six independently implementable, individually reviewed milestones (schema →
store → builder → retrieval → API → documentation).

---

## 2. Responsibilities

### 2.1 What HME OWNS
- Its **satellite tables** — reasoning, embeddings, aggregates, (optional) news snapshots —
  and every migration that creates or extends them.
- The **Memory Record**: the canonical composed view of one historical decision, including
  its `schema_version` and metadata envelope.
- **Retrieval semantics**: by symbol, timeframe, regime, sector, model version, feature
  version, confidence, outcome, and time range; plus pagination and result ordering.
- **Aggregation / rollups** used by Performance Analytics (win rate, avg R, expectancy,
  profit factor, sample size) sliced by any supported dimension.
- The **similarity retrieval contract** — the interface and the vector storage. (The vector
  *algorithm* belongs to the Similarity Engine, Vol 14. HME owns storage + contract.)
- The **enrichment pipeline** ("Memory Builder") including the **backfill** path.

### 2.2 What HME READS
- The `predictions` table — **exclusively through `PredictionStore` read methods**
  (`get`, `list_completed`, `list_active`, `statistics`). No direct SQL against
  `predictions` from HME code, so Sprint 1 keeps ownership of its table.
- Its own satellite tables.
- At build time only, **already-computed** enrichment outputs (e.g. the sentiment/news
  summary, intelligence/sector context) — snapshotted as facts, never recomputed as
  predictions.

### 2.3 What HME WRITES
- Rows in **its own satellite tables only**.
- Nothing else. Ever.

### 2.4 What HME NEVER CHANGES
- Any column of the `predictions` table (not creation fields, not lifecycle fields).
- The Prediction Engine (`app/ai/sklearn_model.py`) and Outcome Engine
  (`app/ai/outcome_model.py`) — never imported, never called (ADR 0002/0003, enforced by
  AST import-guard tests as in Sprint 1).
- The Risk Engine, Resolver, Monitor, Forward Testing Engine, or `/forward/*` API.
- Model artifacts. Migration `0001`. Any previously applied migration.

### 2.5 Interaction with Forward Testing
Forward Testing **produces**; Historical Memory **consumes**. The trigger is a prediction
reaching a **terminal state**. Two mechanisms, deliberately layered:

| Mechanism | Description | Coupling to Sprint 1 |
|---|---|---|
| **Backfill (primary, default)** | HME periodically/on-demand scans for resolved predictions that have no memory yet and enriches them. | **Zero** — no Sprint 1 code changes. Correct even for predictions resolved before HME existed. |
| **Post-resolution hook (optimisation, optional)** | The monitor calls `on_resolved(prediction_id)` after a successful resolution for near-real-time enrichment. | One additive, optional call. Only considered *after* the monitor is wired into the app (Sprint 1 tech debt). |

**Justification:** making backfill the primary path means Sprint 2 needs **no modification
to frozen Sprint 1 code** to be correct and complete. The hook is a latency optimisation
that can be added later without redesign. This directly honours "do not redesign these
components."

---

## 3. Architecture

### 3.1 Layer responsibilities (the requested chain)
```
  Prediction Engine ─┐
  Outcome Engine   ──┼─► produce model outputs (immutable, never imported by HME)
  Risk Engine      ──┘
          │
          ▼
  FORWARD TESTING ........ owns the fact: records a recommendation, resolves it against
          │                real future price, writes the immutable `predictions` row.
          │                (Sprint 1 — frozen.)
          ▼  reads only (via PredictionStore), on terminal state
  HISTORICAL MEMORY ...... owns the knowledge: enriches each completed prediction with
          │                reasoning + metadata + embedding slot, maintains aggregates,
          │                and serves composed Memory Records + retrieval. Stores facts;
          │                never creates them.
          ▼  Memory Records + aggregates (read model)
  DECISION INTELLIGENCE .. owns the judgement: "given history, what does this setup mean?"
          │                Explains a current setup using past outcomes.
          ▼
  SIMILARITY ENGINE ...... owns the algorithm: computes/compares embeddings to answer
          │                "when did we last see this?" Stores vectors in HME's tables.
          ▼
  GPT ASSISTANT .......... owns the narration: reads Memory Records as grounding and
          │                explains them in language. Reads memory; never writes it,
          │                never predicts.
          ▼
  LEARNING ENGINE ........ owns adaptation: trains/evaluates on the labelled history HME
                           serves. Any resulting model change goes through the model
                           process (ADR 0002/0003) — never through HME.
```

### 3.2 Internal structure
```
                    ┌──────────────── HISTORICAL MEMORY ────────────────┐
   predictions ───► │ MemoryBuilder   enrich resolved prediction        │
   (read-only via   │                 → satellites + aggregates         │
    PredictionStore)│                 → backfill(); on_resolved() hook   │
                    │ MemoryStore     CRUD over satellite tables ONLY   │
                    │ RetrievalEngine compose Memory Record; filters;   │
                    │                 aggregates; similarity contract   │
                    └──────────┬───────────────────────────────────────┘
                               │ Memory Record (composed on read)
                               ▼
                        /memory/* REST API  ──►  consumers (§3.1)
```

### 3.3 Design principles (each justified)
| Principle | Justification |
|---|---|
| **Satellite tables, not a duplicate record table** | One source of truth per field; no dual-write drift; `predictions` stays untouched; satisfies "only extend through migrations". |
| **Compose the Memory Record on read** | A join is cheap at our scale and removes an entire class of consistency bugs. Storage cost of duplication avoided. |
| **Derived data is rebuildable** | Aggregates and embeddings can be dropped and recomputed from source; a bad rollup is never data loss. |
| **Idempotent enrichment (keyed by `prediction_id`)** | Matches Sprint 1's posture; safe to re-run builder/backfill any number of times. |
| **Access `predictions` only via `PredictionStore`** | Keeps Sprint 1 the owner of its table; HME cannot accidentally write it. |
| **Versioned Memory Record (`schema_version`)** | Consumers can evolve independently; old readers keep working when fields are added. |
| **Additive-only schema** | New tables and new *indexes* only; never a column change, never a data migration on `predictions`. |

---

## 4. Data Model

All in the existing **`data/prediction_history.db`**. Every change is a **new** `Migration`
appended to the existing runner. Nothing below is implemented in this sprint's planning
phase; these are design specifications, not SQL.

### 4.1 The composition decision (justified)
| Option | Verdict |
|---|---|
| (a) A fat `memory_records` table duplicating all prediction fields | ❌ Two sources of truth; dual-write drift; storage duplication; violates the spirit of "never modify prediction results" (it copies them, then they can disagree). |
| (b) Widen the `predictions` table with memory columns | ❌ Modifies Sprint 1's frozen table; risks its immutability guarantees. |
| **(c) Satellite tables keyed on `prediction_id` + a composed read model** | ✅ **Chosen.** Zero duplication, zero drift, `predictions` untouched, purely additive, trivially rebuildable. |

### 4.2 Existing table (read-only for HME)
**`predictions`** — PK `prediction_id`. The canonical fact table. HME **reads** it and
treats it as immutable. It is the parent of every satellite table via `prediction_id`.

### 4.3 New satellite tables (design)

#### T1 · `memory_reasoning` — the "why" behind the decision
- **Purpose:** structured reasoning and the decision-time confidence narrative — which gates
  fired, which factors drove the call, the rule-checklist snapshot. `predictions` holds the
  *numbers* (`outcome_prob`, `decision_score`); this holds the *explanation*. Required by
  GPT grounding, Decision Intelligence and failure analysis.
- **Primary key:** `prediction_id` (1:1 with a prediction — one decision, one rationale).
- **Foreign key:** `prediction_id` → `predictions.prediction_id`.
- **Fields (conceptual):** `prediction_id`, `created_at`, `confidence` (mirror of the
  decision-time numeric confidence, denormalised for query convenience), `rationale` (text),
  `factors` (structured JSON: driver → contribution/label), `rule_check` (JSON snapshot of
  the My-Rules result), `schema_version`.
- **Indexes:** PK only, plus an index on `confidence` (supports "search by confidence"
  without scanning JSON).
- **Justification of 1:1:** a prediction is made once; its rationale is a property of that
  moment. Versioned re-explanations, if ever needed, become a new satellite — not a
  mutation.

#### T2 · `memory_embeddings` — the future-similarity placeholder
- **Purpose:** vector representation of a historical decision, for the future Similarity
  Engine. **Nothing computes embeddings in Sprint 2**; HME owns the *storage and contract*
  so Similarity can later populate it without a schema redesign.
- **Primary key:** surrogate `embedding_id`, with a **unique constraint on
  `(prediction_id, embedding_kind)`**.
- **Foreign key:** `prediction_id` → `predictions.prediction_id`.
- **Fields (conceptual):** `embedding_id`, `prediction_id`, `embedding_kind` (e.g.
  `context_v1`), `model_name`, `dim`, `vector` (binary blob — packed float32),
  `created_at`.
- **Indexes:** the unique pair above; an index on `embedding_kind`.
- **Justification of many-per-prediction:** embedding models change. Allowing multiple
  *kinds* per prediction means a new embedding model coexists with the old one, and
  retrieval can pick a kind — no destructive recompute, consistent with independent
  versioning (ADR 0002/0003 philosophy applied to vectors).
- **Explicit limitation:** SQLite has no native vector index. Retrieval will be brute-force
  over a **pre-filtered candidate set** (§5.3) with the cap logged. The field shape is
  chosen so a later move to `sqlite-vss` or pgvector is a migration, not a redesign.

#### T3 · `memory_aggregates` — pre-computed rollups for Performance Analytics
- **Purpose:** answer "how have setups like this performed?" in O(1) instead of scanning
  history on every dashboard/API/GPT request.
- **Primary key:** composite `(dimension, bucket, model_version)` — e.g.
  `('sector', 'Energy', 'pred-2025-11')`.
- **Foreign keys:** none (a rollup is not a child of one prediction).
- **Fields (conceptual):** `dimension` (`overall|symbol|sector|timeframe|regime|
  confidence_bucket|outcome`), `bucket`, `model_version`, `n_resolved`, `wins`, `losses`,
  `win_rate`, `avg_r`, `expectancy`, `total_r`, `profit_factor`, `max_drawdown_r`,
  `avg_holding_bars`, `updated_at`.
- **Indexes:** PK covers primary lookups; secondary index on `dimension`.
- **Justification:** fully **derived** — droppable and rebuildable from `predictions` at any
  time, so correctness never depends on it. Keyed by `model_version` so a model swap does not
  silently blend two models' performance into one number (directly supports Model Registry).

#### T4 · `memory_news` — news snapshot reference *(optional, deferrable)*
- **Purpose:** the news/sentiment context at decision time, for explanation and future
  learning.
- **Primary key:** `prediction_id` (1:1).
- **Fields (conceptual):** `prediction_id`, `captured_at`, `summary`, `sentiment_score`,
  `article_count`, `sources` (JSON of ids/titles/urls), `content_hash`.
- **Justification & constraint:** stores a **summary + references + hash only — never full
  article text** (licensing and size). Marked optional: it is the one satellite not required
  by the canonical record list; if descoped, M1 simply omits it with no impact elsewhere.

### 4.4 New indexes on the existing `predictions` table (additive only)
Retrieval (§5) requires filtering by regime, sector, model version, feature version and
outcome — none of which are indexed today (`0001` indexes the uniqueness tuple, `status`,
and `(symbol, created_at)`).

- **Proposed (design):** composite indexes supporting the common retrieval paths — e.g.
  `(sector, status)`, `(market_regime, status)`, `(prediction_model_version, status)`,
  `(timeframe, created_at)`.
- **Justification and safety:** an index is **pure metadata** — it adds no column, changes no
  row, and cannot alter a prediction's results. It is therefore fully compatible with
  "never modify prediction results" and "only extend through migrations". Final index set is
  chosen in M1 against measured query plans (§8), not guessed.
- **Cost:** additional write cost per insert and disk per index — quantified in §8 and
  deliberately kept to the few indexes retrieval actually needs.

### 4.5 Relationship summary
```
predictions (1) ──┬── (0..1) memory_reasoning
                  ├── (0..1) memory_news            [optional]
                  └── (0..n) memory_embeddings      [one per embedding_kind]

memory_aggregates ── derived from predictions (no FK; rebuildable)
```
Referential integrity is enforced by foreign keys (the connection already enables
`PRAGMA foreign_keys=ON`); satellites are only written for predictions that exist.

---

## 5. The Historical Memory Record & Retrieval

### 5.1 Canonical Memory Record (composed on read)
Every requested field, and where it comes from. **No Forward Testing field is removed or
altered** — the record is a superset assembled over them.

| Requested field | Source | Notes |
|---|---|---|
| Prediction (direction, probability) | `predictions` | verbatim model output |
| Outcome | `predictions` | `status`, `resolution_reason`, `resolved_at`, `resolved_price` |
| Trade Result | `predictions` | derived label from `status` (WIN/LOSS/EXPIRED) |
| Realised R | `predictions` | `realised_r` |
| Holding Period | `predictions` | `holding_bars` |
| Market Regime / Market Phase | `predictions` | |
| Sector · Session · Timeframe · Volatility | `predictions` | |
| Confidence | `predictions` (+ mirrored in T1) | `outcome_prob` / `decision_score` |
| Prediction / Outcome / Feature version | `predictions` | three independent stamps |
| Decision Score · Recommendation | `predictions` | |
| Risk Information | `predictions` | `entry`, `stop`, `target1`, `target2` |
| Context | `predictions.context_json` | free-form, forward-compatible |
| **Reasoning** | **T1 `memory_reasoning`** | rationale, factors, rule-check |
| **Metadata** | **assembled** | `schema_version`, build timestamps, provenance, source |
| **Embeddings placeholder** | **T2 `memory_embeddings`** | `null` until Similarity populates it |
| News summary *(optional)* | T4 | summary + references only |

The record shape includes the embedding slot **from day one** so consumers can code against
the final contract while it is still `null`.

### 5.2 Retrieval design (architecture only)
| Capability | Mechanism | Backing |
|---|---|---|
| Search by symbol | equality filter | existing `(symbol, created_at)` index |
| Search by timeframe | equality filter | proposed `(timeframe, created_at)` |
| Search by market regime | equality filter | proposed `(market_regime, status)` |
| Search by sector | equality filter | proposed `(sector, status)` |
| Search by model version | equality on `prediction_model_version` / `outcome_model_version` | proposed index |
| Search by feature version | equality on `feature_version` | covered by version index set |
| Search by confidence | range filter on the mirrored `confidence` in T1 (or `outcome_prob`) | T1 index |
| Search by outcome | equality on `status` (WIN/LOSS/EXPIRED) | existing `status` index |
| Combined filters + time range + pagination | AND-composed predicates, deterministic ordering (`created_at DESC, prediction_id`), limit/offset or keyset | as above |
| **Similar prediction retrieval** | **contract in Sprint 2**, algorithm in Vol 14 | §5.3 |
| **GPT context retrieval** | a bounded, token-aware bundle: N most relevant records + the matching aggregate slice + explicit sample sizes | composition over the above |

**Justification for GPT retrieval being a first-class shape:** the assistant must be
*grounded*, and grounding fails in two ways — too much context (token blow-up) and
context without sample size (invites over-claiming). The retrieval layer therefore returns a
**bounded** set plus the aggregate and its `n`, so the assistant can only say honest things.

### 5.3 Similarity retrieval — contract now, algorithm later
- Sprint 2 defines the interface (`given a prediction/context, return K most similar
  historical records with scores`) and returns an explicit **"similarity engine not
  available"** state until Vol 14 lands. No fake results, ever.
- Execution model when it does land: **filter first, then compare** — narrow by cheap
  indexed predicates (symbol/sector/regime/timeframe), then brute-force cosine over that
  bounded candidate set. Any cap applied is **logged and reported**, never silently applied.
- **Justification:** with SQLite there is no ANN index; a pre-filter keeps brute force
  bounded and, at our volumes (§8), well within acceptable latency.

---

## 6. File Structure (planned)

```
app/memory/                       ← new package, peer of app/forward_testing/
  __init__.py                     public exports
  models.py                       MemoryRecord + satellite dataclasses + enums
  store.py                        MemoryStore — CRUD over satellite tables ONLY
  builder.py                      MemoryBuilder — enrich, aggregate, backfill, hook
  retrieval.py                    RetrievalEngine — compose record, filters, similarity contract
  aggregates.py                   rollup computation (pure functions)
app/api/memory.py                 ← new APIRouter, /memory/* (mounted in api/main.py)
app/database/migrations.py        ← APPEND new migrations only (0001 never edited)
tests/test_memory_*.py            ← per-milestone test modules
docs/api/historical-memory.md     ← M6
docs/architecture/13-historical-memory.md  ← M6 (status + as-built)
```
Only two existing files are touched across the whole sprint: `migrations.py` (append-only)
and `api/main.py` (mount the router) — both additive, mirroring exactly how Sprint 1 was
integrated.

---

## 7. Migration Plan

**Mechanism:** the existing runner — forward-only, append-only, idempotent, transactional,
recorded in `schema_migrations`. `0001` is never edited.

| # | Migration (planned) | Content | Risk |
|---|---|---|---|
| `0002` | reasoning | T1 + its indexes | none (new table) |
| `0003` | embeddings | T2 + unique/kind indexes | none (new table) |
| `0004` | aggregates | T3 + index | none (new table) |
| `0005` | retrieval indexes | additive indexes on `predictions` | low (metadata only; write-cost noted) |
| `0006` | news *(optional)* | T4 | none (new table) |

**Rules:** one concern per migration (so a single one can be reviewed/reverted in isolation);
`IF NOT EXISTS` semantics for idempotency; every migration verified against (a) a fresh
database and (b) a **populated Sprint-1 database**, asserting `predictions` is byte-for-byte
unchanged and all Sprint 1 tests still pass.

**Rollback posture:** SQLite cannot cheaply drop columns, but every Sprint 2 object is a
*new table or index*, so rollback is a clean drop with **zero** impact on `predictions`.
This is a deliberate benefit of the satellite design.

---

## 8. Performance

### 8.1 Record growth (estimates, stated as scenarios — not predictions)
Only actionable BUY/SELL recommendations are recorded, so volume is driven by watchlist
size × timeframes × selectivity.

| Scenario | Predictions/day | Per year | `predictions` size/yr¹ | + satellites² | + embeddings³ |
|---|---|---|---|---|---|
| Conservative (NSE daily, selective) | ~10 | ~3.6 k | ~4 MB | ~+2 MB | ~+5 MB |
| Moderate (multi-market, few timeframes) | ~100 | ~36 k | ~40 MB | ~+18 MB | ~+55 MB |
| Aggressive (broad intraday screening) | ~1 000 | ~365 k | ~400 MB | ~+180 MB | ~+550 MB |

¹ ~1 KB/row incl. `context_json`. ² reasoning + aggregates ~0.5 KB/record.
³ one 384-dim float32 vector ≈ 1.5 KB/record (768-dim ≈ 3 KB).

**Conclusion:** even the aggressive scenario is ~1 GB/year — comfortably within SQLite's
capabilities. **Embeddings dominate storage**, which is why they are a separate table
(droppable/recomputable) rather than a column on the record.

### 8.2 Index strategy
- Index only what retrieval actually filters on (§5.2) — every index costs write time and
  disk. Final set chosen in M1 from measured query plans, not speculation.
- Prefer **composite** indexes matching real predicates (e.g. `(sector, status)`) over many
  single-column indexes.
- Aggregates exist precisely so the common "how did X perform" question never scans history.

### 8.3 SQLite limitations (honest)
| Limitation | Impact | Mitigation |
|---|---|---|
| Single writer | Builder + monitor + API contend on writes | WAL + busy timeout (already in place) + in-process lock; enrichment is batched and off the request path |
| No native vector index | Similarity cannot scale to ANN | filter-then-brute-force over a bounded set; cap logged; vector-extension/pgvector path preserved by the schema shape |
| No cheap `DROP COLUMN` | Schema mistakes are expensive | satellite-only design means rollback is a table drop |
| Single-file DB | Concurrency ceiling, backup granularity | acceptable at projected volumes; Postgres is the exit |

### 8.4 Future PostgreSQL migration
The design is deliberately portable: satellites keyed by id, no SQLite-specific types, all
schema created through the same versioned runner. Migration path (Vol 21, a future sprint):
translate the migration set → move `predictions` + satellites → swap the connection layer →
adopt **pgvector** for `memory_embeddings` (the one component that materially improves).
**Trigger to revisit:** sustained write contention, multi-user concurrency, or a similarity
corpus large enough that brute force exceeds its latency budget.

---

## 9. Testing Strategy

- **Unit**
  - Memory Record composition: prediction + each satellite → correct merged record; missing
    satellites yield `null`/defaults, never an error (the common case early on).
  - Aggregate math (win rate, avg R, expectancy, PF, max DD) against known R-series,
    cross-checked for consistency with `PredictionStore.statistics`.
  - Confidence/outcome bucketing; filter predicate construction; pagination determinism.
  - Idempotency: building the same prediction twice yields exactly one satellite row set.
- **Integration**
  - Temporary DB, end-to-end: seed predictions → resolve → build memory → retrieve record →
    filtered search → aggregates. Assert `predictions` is unchanged.
  - **Backfill:** many resolved predictions with no memory → one pass enriches all; a second
    pass is a no-op.
  - **Empty-state:** every retrieval path behaves correctly with zero memory (the *actual*
    initial condition of this system).
  - **Isolation guards (as in Sprint 1):** AST assertions that `app/memory/*` and
    `app/api/memory.py` import neither `app.ai.sklearn_model` nor `app.ai.outcome_model`; a
    test asserting HME issues no writes to `predictions`.
- **Migration**
  - Fresh DB applies `0001..000N` cleanly; a **populated Sprint-1 DB** migrates forward with
    `predictions` unchanged and all Sprint 1 tests still green; re-running is a no-op;
    partial-failure rolls back (already guaranteed by the transactional runner).
- **Performance**
  - Seeded corpora (~10 k / 100 k records): filtered retrieval and aggregate reads measured
    against stated targets; brute-force similarity measured over a pre-filtered set with the
    candidate cap logged. Any bound applied is reported, never silent.
- **Discipline:** all tests use temporary databases; production `prediction_history.db` is
  never touched.

---

## 10. Risks

| # | Risk | Type | L · I | Mitigation |
|---|---|---|---|---|
| R1 | HME accidentally writes/mutates `predictions` | Architectural | Low · **High** | Satellite-only writes; access `predictions` **only** via `PredictionStore` reads; explicit no-write test; review gate |
| R2 | Duplicated fields drift from source | Architectural | Low · Med | Compose-on-read; the only denormalised value is `confidence` (mirrored deliberately for indexing, rebuildable) |
| R3 | Scope creep — building Similarity/Learning inside HME | Architectural | **Med** · Med | Hard boundary: HME owns storage + contract; algorithms live in Vol 14/15; enforced by milestone scope |
| R4 | Similarity too slow at scale (no ANN in SQLite) | Performance | Med · Med | Filter-then-brute-force; bounded candidates; logged caps; pgvector path preserved |
| R5 | Embedding storage dominates DB size | Storage | Med · Med | Separate table; droppable/recomputable; `dim`/`kind` versioned; retention policy possible |
| R6 | Index additions slow inserts / bloat | Performance | Med · Low | Only indexes retrieval needs; chosen from measured plans; quantified in §8 |
| R7 | Migration on a populated DB fails or locks | Migration | Low · **High** | Transactional idempotent runner; one concern per migration; tests against a seeded Sprint-1 DB; WAL + busy timeout |
| R8 | Aggregates become stale/wrong | Correctness | Med · Med | Fully derived + a rebuild path; never the source of truth |
| R9 | Building on an almost-empty memory misleads design | Product | **Med** · Med | Empty-state tests are first-class; retrieval always returns sample size; no aggregate is presented as insight below threshold |
| R10 | News snapshots bloat DB / licensing | Storage/Legal | Low · Med | Summary + references + hash only; never full text; satellite is optional |
| R11 | Enrichment source slow/failing at build time | Reliability | Med · Low | Build is off the request path; a failed enrichment degrades to a partial record, never blocks or loses a prediction |

---

## 11. Milestone Breakdown

The suggested M1–M6 structure is sound and adopted, with two refinements (justified below).

| M | Title | Scope | Deliverables | Depends on |
|---|---|---|---|---|
| **M1** | Database Extension | Migrations for T1–T3 (+T4 optional) and the additive `predictions` indexes; satellite dataclasses + enums; `schema_version` | migrations, models, migration tests (incl. populated Sprint-1 DB) | — |
| **M2** | Memory Store | `MemoryStore`: CRUD over satellite tables only; idempotent upserts keyed by `prediction_id`; thread-safe like `PredictionStore` | store + unit tests | M1 |
| **M3** | Memory Builder | `MemoryBuilder`: enrich a resolved prediction → satellites; incremental **aggregate** maintenance; **backfill**; optional post-resolution hook | builder + integration tests | M2 |
| **M4** | Retrieval Engine | Compose Memory Record; all §5.2 filters + pagination; aggregate reads; **similarity contract** (explicit "unavailable"); GPT context bundle shape | retrieval + tests | M3 |
| **M5** | REST API | `/memory/*` router mounted in `api/main.py`; validation; error handling; honest sample-size reporting | router + API tests | M4 |
| **M6** | Documentation | Vol 13 as-built, Vol 21 (new tables), API reference, sprint report, ADRs for Sprint 2 decisions | docs | M5 |

**Refinements (justified):**
1. **Aggregates live in M3, not a separate milestone** — they share the resolution trigger
   and the same write path as enrichment; splitting them would put two halves of one
   transaction either side of a review gate.
2. **M4 owns both filter-retrieval and the similarity *contract*** — so M5's API is a thin
   mount (ADR 0006) rather than a milestone that has to invent retrieval semantics.

Each milestone: implement only that milestone → full suite green → prove engines and Sprint 1
untouched → update docs in the same commit (docs-before-push) → commit + push → **STOP for
review**.

---

## 12. Estimated Sprint Scope

Relative sizing to aid planning — not a schedule commitment.

| M | New files | Existing files touched | Est. tests | Complexity | Main risk |
|---|---|---|---|---|---|
| M1 | 1–2 (`models.py`) | `migrations.py` (append) | ~25–35 | Medium | R7 (migration on populated DB) |
| M2 | 1 (`store.py`) | — | ~30–40 | Low–Med | R1 (write isolation) |
| M3 | 2 (`builder.py`, `aggregates.py`) | — | ~30–40 | **High** | R8, R11 (aggregates, enrichment failure) |
| M4 | 1 (`retrieval.py`) | — | ~30–40 | Med–High | R4, R9 (similarity, empty state) |
| M5 | 1 (`app/api/memory.py`) | `api/main.py` (mount) | ~20–30 | Low–Med | — |
| M6 | docs only | docs | — | Low | — |

**Totals:** ~6–8 new modules, **2** existing files touched (both additively), **~135–185 new
tests**, 4–6 migrations. Comparable in shape to Sprint 1 (128 tests, 6 milestones), with the
complexity concentrated in M3 (enrichment + aggregates) and M4 (retrieval).

**Out of scope for Sprint 2 (explicitly):** computing embeddings (Vol 14), any learning or
retraining (Vol 15), Decision Intelligence logic, a memory dashboard UI, Postgres migration,
and any change to Sprint 1 or the engines.

---

## 13. Definition of Done

Sprint 2 is complete when:
1. Every completed prediction can be retrieved as a full **Memory Record** (with `null`
   embeddings until Similarity lands).
2. All §5.2 retrieval paths work, with pagination and honest sample sizes.
3. Aggregates answer "how have setups like this performed?" per dimension and model version.
4. Backfill enriches historical predictions that resolved before HME existed.
5. `predictions` is provably unchanged; the Prediction and Outcome engines are provably
   un-imported and unmodified; all Sprint 1 tests still pass.
6. Downstream consumers (Similarity, GPT, Learning, Decision Intelligence, Model Registry,
   Portfolio Intelligence, Performance Analytics) have a **stable, documented contract** to
   build against.

---

## Deliverables checklist (this document)
1. ✅ Executive Summary · 2. ✅ Responsibilities · 3. ✅ Architecture Diagram ·
4. ✅ Data Model · 5. ✅ File Structure · 6. ✅ Migration Plan · 7. ✅ API Design *(see
companion note below)* · 8. ✅ Testing Strategy · 9. ✅ Risks · 10. ✅ Milestone Breakdown ·
11. ✅ Estimated Sprint Scope.
12. ⏳ **Awaiting approval. No implementation begins — including Milestone 1 — until this
plan is explicitly approved.**

---

## Appendix A — REST API design (`/memory/*`, design only, not implemented)

Mounted beside `/forward/*`, following ADR 0006 (thin transport over the domain). Read-only
except explicit build/rebuild operations.

| Method · Path | Purpose | Key inputs | Notes |
|---|---|---|---|
| `GET /memory/record/{prediction_id}` | **Memory details** — one full record | — | 404 if unknown |
| `GET /memory/search` | **Search + filters** | `symbol`, `timeframe`, `sector`, `regime`, `outcome`, `model_version`, `feature_version`, `confidence_min/max`, `from`/`to`, `limit`, `cursor` | all optional, AND-composed; deterministic ordering |
| `GET /memory/statistics` | **Statistics** | `dimension`, optional `bucket`, `model_version` | served from aggregates; always returns `n` |
| `GET /memory/timeline` | **Timeline** | `symbol`/`from`/`to`, `limit` | chronological history of decisions + outcomes |
| `GET /memory/similar/{prediction_id}` | **Similar memories** | `k`, filters | returns an explicit *unavailable* state until Vol 14 |
| `GET /memory/context` | **GPT context bundle** | `symbol`, `k`, token budget | bounded records + matching aggregate + sample sizes |
| `POST /memory/build/{prediction_id}` | enrich one resolved prediction (idempotent) | — | ops/backfill |
| `POST /memory/backfill` | enrich all resolved-but-unenriched | `limit` | idempotent; reports counts |
| `POST /memory/rebuild-aggregates` | recompute rollups from source | — | idempotent repair path |

**Not designed, by principle:** any endpoint that creates a prediction, runs a model, or
writes to `predictions`. Historical Memory stores facts; it never creates them.
