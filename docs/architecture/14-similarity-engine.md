# Volume 14 — Similarity Engine

## Purpose
Answer *"I have seen this setup before"* — find the most similar historical situations and
report how trades actually fared in them. Pure **explainability**, honestly labelled.

## Status: 🟡 Two implementations. **(1) Legacy explainability** — `app/ai/similarity_engine.py`
(kNN over intelligence features, folded into `/intelligence`; **context only, no edge** —
below). **(2) Sprint 3 build over Historical Memory** — a new `app/similarity/` package that
will fill the `memory_embeddings` placeholder and answer the `/memory/similar` contract.
**M1 (Feature Vector Builder) + M2 (Embedding Generator) delivered**; similarity search,
retrieval integration, and API are later milestones. See the **[Sprint 3
plan](sprints/sprint-03-similarity-plan.md)** for the full milestone breakdown, and the
M1/M2 sections below.

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
