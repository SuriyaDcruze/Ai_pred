# Volume 14 — Similarity Engine

## Purpose
Answer *"I have seen this setup before"* — find the most similar historical situations and
report how trades actually fared in them. Pure **explainability**, honestly labelled.

## Status: 🟢 Sprint 3 COMPLETE (`v0.3.0-similarity-engine`). Two implementations:
**(1) Legacy explainability** — `app/ai/similarity_engine.py` (kNN over intelligence features,
folded into `/intelligence`; **context only, no edge** — below). **(2) Sprint 3 build over
Historical Memory** — the `app/similarity/` package + `/memory/similar*` API: feature vectors →
embeddings → cosine k-NN → retrieval integration → REST, all deterministic and live. Built as
**explainability, not a predictive edge**; populated only as Forward Testing accumulates a live
corpus. 98 tests; full suite 567 passed. See the [Sprint 3 report](../sprints/sprint-03-report.md)
and [plan](sprints/sprint-03-similarity-plan.md).

### Pipeline (as built, Sprint 3)
```
Memory Record ─► FeatureVectorBuilder (sim-fv-1, dim 100) ─► EmbeddingGenerator (sim-emb-1, L2)
   ─► memory_embeddings ─► SimilaritySearch (sim-search-1, cosine k-NN) ─► RetrievalEngine (DI)
   ─► /memory/similar* REST API
```

### Deterministic guarantees, versioning & limitations
- **Deterministic end-to-end:** same Memory Record → same feature vector → same embedding →
  same ranked neighbours (stable sort `-similarity`, then `prediction_id`). Stable SHA-1 hashing
  (never salted `hash()`); no randomness; no training; no inference.
- **Versioning:** `feature_version` `sim-fv-1` · `embedding_version` `sim-emb-1` (packed with the
  feature version in `model_name`) · `search_version` `sim-search-1`. Any change to encoding,
  embedding transform, or ordering requires a **new** version; old vectors coexist by
  `embedding_kind`. `GET /memory/similar/health` reports all versions + dimension.
- **Limitations:** brute-force over a candidate set (no ANN in SQLite; logged cap; pgvector is
  the scale path, Vol 21); near-empty live corpus until Forward Testing feeds it; **no
  predictive edge claimed** — explainability only.

### As built — Sprint 3 · M5 (Similarity REST API)
`app/api/similarity.py` — a **thin transport** router (`/memory/similar*`) over the M3 search
engine + M4 retrieval integration; no search algorithm in the API, no direct DB access, no
engine imports (asserted). The `SimilaritySearchEngine` is created in the app lifespan and
**injected into `RetrievalEngine`** (via M4's setter), so the live endpoints work. This router
**owns every `/memory/similar*` route** (moved out of `app/api/memory.py`) so `/health` and
`/search` are matched before the `/{prediction_id}` catch-all.
- **Endpoints:** `GET /memory/similar/{prediction_id}`, `GET /memory/similar?prediction_id=`
  (query form), `POST /memory/similar/search` (body), `GET /memory/similar/health`. All search
  forms accept `top_k`, `threshold`, and the candidate filters (symbol/sector/timeframe/
  regime/phase/outcome/model/feature version).
- **Response:** `available`, `reason`, `prediction_id`, `neighbours` (id, score, outcome,
  realised R, confidence, holding period, market metadata, versions), `sample_size`, honest
  `summary` (win rate / avg R / outcome distribution), `versions` (embedding/feature/search +
  dimension), `metadata`. **Never** exposes raw embeddings, feature vectors, or internal
  hashing.
- **Errors:** `400` validation (top_k/threshold/missing target), `404` prediction/embedding
  not found, `409` version mismatch, `503` Similarity Engine unavailable. (FastAPI type
  coercion still yields `422`, the project standard.) Structured logging of endpoint + timing +
  status — never embeddings. Pydantic models + generated OpenAPI. 19 API tests (every endpoint,
  validation, errors, empty corpus, unavailable engine, determinism, OpenAPI, no-vector-leak).
- **Note:** the placeholder `/memory/similar/{id}` was **moved** from `app/api/memory.py` to
  this router (route ownership); the two Sprint-2 similarity API tests moved with it. The app’s
  other `/memory/*` behaviour is unchanged.

### As built — Sprint 3 · M4 (Retrieval integration)
The similarity contract is now activated by **injecting** the M3 engine into
`RetrievalEngine` — the one deliberate, **additive** touch of a Sprint 2 file
(`app/memory/retrieval.py`); all existing retrieval behaviour is unchanged (backward
compatible — Sprint 2's 47 retrieval/API tests still pass).
- **Dependency injection (setter), no import cycle.** `RetrievalEngine` gains an optional
  `similarity_engine` (constructor kwarg + `set_similarity_engine()`). Because the engine
  depends on the retrieval engine, it is created second and injected — breaking the
  construction cycle. `retrieval.py` imports **nothing** from `app.similarity` at module load
  (duck-typed engine + a lazy import inside the method), so there is no import cycle (asserted
  by a top-level-imports test).
- **Activation + graceful fallback.** `similar(prediction_id)`: no engine → the documented
  *"Similarity Engine unavailable"* (unchanged); engine present → delegates to
  `search_by_prediction` and returns the mapped result. `similar_by_embedding(embedding)`
  delegates to `search`. Typed errors (`MissingEmbeddingError`, unsupported version, malformed
  request) surface; an **unexpected** engine failure degrades gracefully to *unavailable*
  rather than propagating.
- **Response contract** (`SimilarityResult`, extended additively): `available`, `reason`,
  `results` (neighbours), `sample_size`, `summary` (win rate / avg R / outcome distribution),
  `metadata` (similarity + feature version, metric, candidate count, cap flag). **No raw
  vectors** or internal feature representations. Read-only + thread-safe; structured logging of
  enabled/disabled, neighbour count, timing, fallback — never embeddings.
- **Not in M4:** the running app is **not** wired to a similarity engine (so the live
  `/memory/similar` still returns *unavailable*) — app wiring + the richer API response are
  M5. 17 integration tests (enabled/disabled, fallback, DI, empty corpus, missing embedding,
  unknown prediction, determinism, concurrency, no-writes, backward compatibility).

### As built — Sprint 3 · M3 (Similarity Search Engine)
`app/similarity/search.py` — **read-only** cosine k-NN over the embeddings stored in
`memory_embeddings` (M2). It performs **no writes**, generates no embeddings, retrains
nothing, modifies neither Historical Memory nor `predictions`, exposes no HTTP, and imports
neither engine (asserted). It reports **facts only** — never a recommendation.
- **Metric:** cosine similarity (`cosine_similarity`) — identical direction `1.0`, orthogonal
  `0.0`, opposite `-1.0`; a zero vector yields `0.0`. Embeddings are unit-length (M2), so
  cosine = dot product.
- **Algorithm `sim-search-1` — filter-first, then brute-force:** candidates are narrowed by
  Memory-Record predicates (symbol, sector, timeframe, market regime, **market phase**,
  outcome, prediction-model / feature version — phase applied in-app since `MemoryFilter` lacks
  it), each candidate embedding is compared by cosine, thresholded by `min_similarity`, and
  sorted **deterministically** (`-similarity`, then `prediction_id`); the top *k* are returned.
- **Candidate cap:** bounds the brute-force set; when the cap bites it is **logged** and
  `cap_applied=True` (never silent). SQLite has no ANN index — pgvector is the scale path.
- **Query forms:** `search_by_prediction(id)` (neighbours of a stored prediction, excludes
  itself; `MissingEmbeddingError` if unembedded) and `search(embedding)` (arbitrary query).
  Candidates of an **incompatible embedding version** are skipped with a logged count.
- **Result:** per-neighbour `prediction_id`, `similarity_score`, confidence, outcome, realised
  R, holding, market metadata, embedding/feature version — **no raw vectors**. Plus an honest
  `SimilaritySummary` (sample size, win rate, avg realised R, outcome distribution).
- **Typed errors:** `MissingEmbeddingError` / `SearchRequestError` (bad k/threshold/cap) /
  `UnsupportedVersionError` / `DimensionMismatchError` / `InvalidFeatureVectorError`. Structured
  logging of timing + candidate/compared/returned counts + version — **never vector values**.
  Read-only + thread-safe. 22 tests (cosine correctness, ranking + tie-break determinism,
  top-k, filtering, cap, threshold, empty corpus, dedup, version/dimension/request validation,
  summary stats, concurrency, no-writes, no-engine-imports).

### As built — Sprint 3 · M2 (Embedding Generator)
`app/similarity/embedding.py` — a **deterministic** transform of a feature vector into an
embedding, stored in `memory_embeddings` via `MemoryStore` (never direct SQL). It performs
**no** similarity search, ranking, API, or training, and modifies no Historical Memory facts
beyond its own embedding rows; imports neither engine (asserted).
- **Strategy `sim-emb-1`:** the **L2-normalised** `sim-fv-1` feature vector (dim 100) — every
  embedding on the unit sphere (well-behaved for later cosine), a zero vector staying zero.
  Fully deterministic: identical feature vector → identical embedding, bit-for-bit; no model
  artifact, no randomness.
- **Metadata:** `embedding_version` (`sim-emb-1`), `feature_version` (`sim-fv-1`),
  `schema_version`, `dimension` (100), `embedding_kind` (`context_v1`), `created_at`. Since
  the frozen `memory_embeddings` table has no version columns, the two versions are packed
  into the row's `model_name` (`"sim-emb-1/sim-fv-1"`); `dimension`→`dim`. Filling
  `embedding_kind="context_v1"` populates the **NULL placeholder** the Memory Builder created.
- **Operations:** `generate_from_feature_vector`, `generate_embedding` (from a Memory Record),
  `store_embedding`, `build_and_store` (idempotent skip), `rebuild_embedding` (overwrite), and
  `backfill_embeddings` (enriched records only; idempotent; one failure never aborts the
  batch). Thread-safe via `MemoryStore`'s lock; idempotent by `(prediction_id, embedding_kind)`
  so concurrent/duplicate generation never creates duplicate rows.
- **Typed errors:** `UnsupportedVersionError` / `DimensionMismatchError` /
  `InvalidFeatureVectorError`. Structured logging of embedding version + dimension + counts —
  **never vector values**. 21 tests (determinism, L2 unit-length, storage, rebuild, backfill
  idempotency, concurrency, invalid/mismatched vectors, rollback, writes-only-embeddings,
  no-engine-imports).

### As built — Sprint 3 · M1 (Feature Vector Builder)
`app/similarity/{models,feature_vector}.py` — a **pure, deterministic** transform from a
Historical Memory Record (`RetrievalEngine.MemoryRecord.to_dict()`) to a versioned numerical
`FeatureVector`. It **does not** generate embeddings, compare vectors, rank similarity, or
modify Historical Memory; it imports neither the Prediction nor Outcome engine (asserted).

- **Feature version `sim-fv-1` → dimension 100** (schema_version 1). Layout is fixed and
  immutable; **any** change to order/vocab/normalization requires a new `feature_version`.
- **Groups (width):** Market 42 — sector (16, stable hash), regime (6), phase (4), volatility
  (4), session (4), timeframe (8); Trade 10 — direction (3), confidence (value+present),
  decision_score, stop/target distance, risk-reward, geometry-present; Outcome 9 — realised R
  (value+present), holding (value+present), trade result (5); Model 24 — prediction/outcome/
  feature version (8-bucket stable hash each); Context 15 — confidence bucket (10), factor
  count, rule counts (n + passed), reasoning-present, embedding-present.
- **Encoding strategy (deterministic + documented):** fixed one-hot **vocabularies** for
  enums (unknown → all-zeros); **stable SHA-1 hashing** into fixed buckets for open
  categoricals (sector, model versions) — never Python's salted `hash()`; **clamped min-max
  scaling** for numerics with documented bands (confidence [0,1]; decision_score [-1,1];
  realised R [-3,5]; holding [0,200]; distances [0,1]; risk-reward [0,10]); explicit
  **present flags** so a missing value is distinct from a real zero.
- **Validation (typed errors):** non-mapping / missing `prediction_id`|`status` →
  `MissingFieldError`/`InvalidMemoryRecordError`; unknown `feature_version` or record schema
  version > supported → `UnsupportedVersionError`. Structured logging of schema/feature
  version + dimension — **never vector contents**. 19 unit tests (determinism, enum + hash
  encoding, normalization, missing/optional fields, invalid records, version rejection,
  dimension stability, real-record integration, no-engine-imports).

## Responsibilities
- For a new setup, find the **k nearest neighbours** in standardised feature space from
  history strictly *before* the query bar.
- Report neighbours' **win rate, average R, count, similarity**.

## Inputs / Outputs
- **In:** current outcome-feature vector; fitted history (features + won + realised R).
- **Out:** `{ n, win_rate, avg_R, similarity }`.

## Architecture
- `SimilarityEngine.fit()` standardises history; `query()` computes Euclidean distance,
  takes the k nearest, returns their outcome stats; similarity = mean 1/(1+distance).
- Fit on the training slice; query the current bar — **no look-ahead**.

## Honest finding (why it's explainability, not edge)
- Tested as a **predictive feature** on the untouched test: it **did NOT add edge**
  (+0.40R → +0.30R — the Outcome Engine already captures it). So it is used **only for
  explanation**: "your setup resembles 20 past ones that won 63% at +0.31R." That framing
  is deliberate and documented.

## API integration
- Folded into `/intelligence` (`historical_similarity`) and the Deep Analysis card.

## Failure / logging
- Too little history (< k) → returns NaN stats gracefully; the report omits the section.

## Testing
- Exercised via the intelligence path; core math is deterministic.

## Prediction-Model integration
- **Context only** — never a model feature, never the decision.

## LLM integration
- The assistant cites the historical analogue when explaining ("similar setups won X%").

## Future
- Larger, cross-stock historical index (from Historical Memory, Vol 13); show the actual
  analogue dates/charts; sector-conditioned similarity.
