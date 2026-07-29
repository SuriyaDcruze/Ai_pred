# Volume 15 — Learning Engine

## Purpose
Turn accumulated experience into (validated) improvement — the **meta-model** that learns
which signals win, and the **nightly champion/challenger retrain** that only promotes a
model that genuinely beats the incumbent.

## Status — two distinct subsystems under "Learning Engine"
> ⚠️ **Disambiguation (Sprint 4).** "Learning Engine" now names **two different** things — do
> not conflate them:
> - **(A) Meta-model retrainer** — `app/training/*` (below). **Trains** a meta-model via the
>   champion/challenger promotion pipeline. 🟡 Built, waiting on data.
> - **(B) Behavioural Learning Engine** — `app/learning/` (Sprint 4, `v0.4.0`, in progress).
>   **Descriptive analytics** over completed Historical Memory — **no training, no inference,
>   read-only**. Surfaces statistically honest, evidence-bound observations; says
>   `INSUFFICIENT_DATA` where history is thin. See
>   `sprints/sprint-04-learning-plan.md`.

### (B) Behavioural Learning Engine — as built (Sprint 4 · M1: Learning Dataset Builder)
`app/learning/{models,dataset}.py` — a **pure, read-only, deterministic** transform from
completed Historical Memory into a versioned **Learning Dataset**, the canonical input for
every later Learning milestone. No statistics/patterns/recommendations/API yet; imports neither
engine (asserted).
- **`LearningDataset`** (`lds-1`/`lrn-1`): ordered `LearningRecord`s (prediction, outcome,
  realised R, win, confidence, holding, timestamps, symbol/sector/timeframe/regime/phase, model
  + feature version, optional similarity metadata, memory reference), `corpus_size`,
  `source_versions`, `generated_at`, `build_duration_ms`, and a **deterministic SHA-256
  checksum** (same corpus → same checksum; volatile fields excluded). Built read-only via
  `RetrievalEngine`; **only completed-with-outcome** trades included.
- **Canonical states** established: `VALIDATED` / `HYPOTHESIS` / `INSUFFICIENT_DATA`. A corpus
  below `min_corpus` (default 30; **zero** included) → `status = INSUFFICIENT_DATA` — the
  expected young-system behaviour, not an error. (Statistical classification is a later
  milestone.)
- **Typed validation:** malformed / incomplete-outcome / inconsistent-timestamp / unsupported-
  version / corrupted-metadata → typed exceptions. Structured logging of corpus size + checksum
  + version + status — never reasoning, embeddings, or feature vectors.
- **Storage foundation:** append-only migration `0006` adds `learning_runs` (run metadata:
  version/corpus/checksum/status) — its **own** table; **no Sprint 1–3 table changed**. M1 is
  read-only, so the builder writes nothing yet. 23 tests (determinism, checksum, ordering,
  filtering, empty/insufficient, validation, migration, concurrency, no-writes, no-engine-
  imports).

**M2 — Pattern Extraction Engine** (`app/learning/patterns.py`): a **pure, read-only**
transform that groups the Learning Dataset into deterministic **candidate patterns** — still
**descriptive only** (no statistics/significance/CIs/recommendations); imports neither engine.
- **`CandidatePattern`** = one recurring condition: a deterministic `pattern_id` (a function of
  version + grouping, never random), `pattern_type`, `grouping_key`/`grouping_value`,
  `evidence_count`, the supporting **`prediction_ids`** (full traceability), `corpus_size`,
  `status`. **Metadata + evidence only.** Every returned pattern is a `HYPOTHESIS`; an
  empty/thin dataset (or no group reaching `min_evidence`) → `INSUFFICIENT_DATA`. **Never
  `VALIDATED`** (that is M3).
- **Dimensions** (extensible registry): symbol, sector, timeframe, market regime/phase,
  confidence bucket, prediction-model / feature version, holding-period bucket, outcome
  category. Groups below `min_evidence` (default 3) are dropped and **counted**
  (`insufficient_groups`), never silent. Deterministic ordering + a SHA-256 checksum → same
  dataset yields identical patterns.
- **Typed validation** (malformed dataset / unsupported version / unknown dimension / duplicate
  id / inconsistent evidence). Structured logging of corpus/pattern counts + version + status —
  never vectors/embeddings/reasoning. Thread-safe, idempotent, writes nothing.
- **Storage foundation:** append-only migration `0007` adds `learning_patterns` (**metadata
  only** — no stat columns); its own table, no Sprint 1–3 table changed. 25 tests.

**M3 — Statistical Validation Engine** (`app/learning/statistics.py`): the **first** milestone
that performs statistics. A **pure, read-only** transform that takes the candidate patterns (M2)
+ the Learning Dataset (M1) and classifies each pattern `VALIDATED` / `HYPOTHESIS` /
`INSUFFICIENT_DATA`. **No recommendations, no REST API, no GPT** (later milestones); imports
neither engine.
- **Reuse, not reinvent (§4.4).** The base rollups (win rate, loss rate, avg R, expectancy,
  profit factor, max drawdown, avg holding) are computed by the **same Sprint 2 aggregate math**
  (`app.memory.aggregates._metrics`, via lightweight record adapters) that fills
  `memory_aggregates` — so a validated pattern's figures **cannot drift** from the stored
  aggregates (a regression test asserts they match exactly). This milestone **adds** only what the
  aggregates lack.
- **Honesty gates.** Every rate carries a **95% Wilson confidence interval** (reported with a
  width + coarse `HIGH`/`MODERATE`/`LOW` quality — never a point estimate alone); a two-sided
  proportion **significance** test vs a baseline (default a coin flip, 0.5); and a
  **multiple-comparison correction** applied across the whole family of tested patterns
  (Benjamini–Hochberg default, Bonferroni, or none — an **extensible registry**; the strategy is
  recorded on the run). A pattern is `VALIDATED` **only** when it clears `min_sample`, is
  significant **after** correction, **and** its interval excludes the baseline; below the sample
  floor → `INSUFFICIENT_DATA`; otherwise `HYPOTHESIS`. **Weak evidence is never promoted.**
- **`ValidatedPattern`** (the statistical result): stable `pattern_key` (= the deterministic M2
  identity), versions, sample size, wins/losses, win/loss rate, avg R, expectancy, profit factor,
  drawdown, holding, the `ConfidenceInterval`, the `Significance` (raw p-value/z + verdict), the
  correction method + corrected verdict, a **consistency score** (win-rate stability across
  chronological sub-periods — flags curve-fit-to-one-window patterns), status, and
  `evidence_count`. A thin/empty corpus → run status `INSUFFICIENT_DATA` (fabricates nothing).
- **Deterministic + thread-safe + idempotent:** identical dataset + patterns + config → identical
  results (SHA-256 checksum over the ordered validated patterns). Structured logging of validation
  duration, validated/rejected counts, correction, thresholds, corpus size — never reasoning,
  vectors, or embeddings.
- **Storage foundation:** append-only migration `0008` adds `learning_pattern_stats` (statistics +
  CI + significance + correction columns); its own table, no Sprint 1–3 table changed. The
  validator is **read-only** (writes nothing yet). 29 tests (primitives, determinism, CI,
  significance, correction strategies, sample floor, hypothesis-vs-validated, insufficient/empty,
  corrupted/malformed/version, concurrency, **regression vs Sprint 2 aggregates**, migration +
  round-trip, no-writes, no-engine-imports).

**M4 — Recommendation Engine** (`app/learning/recommendations.py`): a **pure, read-only,
deterministic** transform that turns the **VALIDATED** patterns (M3) into evidence-bound
**descriptive** recommendation objects. It performs **no statistics** (it restates M3's figures),
**never** predicts, trains, or gives trading advice, exposes no HTTP; imports neither engine.
- **`Recommendation`** = one auditable historical observation: deterministic `recommendation_id`
  / `recommendation_key` (= `learning_version|pattern_key|recommendation_type`) / sha256
  `recommendation_hash`; the `pattern_key`/`pattern_hash` it describes; a `recommendation_type`
  (`HISTORICAL_STRENGTH` / `HISTORICAL_WEAKNESS` / `UNSTABLE_BEHAVIOUR`, extensible) and a
  dimension-based `recommendation_category` (Sector/Regime/Timeframe/Symbol/Confidence/Risk/Model
  Observation); plain-language `title`/`summary`/`detailed_explanation`/`statistical_basis`; the
  M3 `ConfidenceInterval` + `Significance` verbatim; a `consistency_score`; the supporting
  **`prediction_ids`** (sourced from the **M2 candidate patterns**, so M3's output is *not*
  modified to carry evidence); and an always-non-empty `limitations` list.
- **Communication confidence ≠ significance.** `recommendation_confidence` (`HIGH`/`MEDIUM`/`LOW`)
  is confidence in *communicating* the observation, from a deterministic 0–7 rubric over four
  evidence-quality factors — sample size, CI width, consistency, evidence traceability — **not**
  the p-value. A statistically significant but small/wide-CI pattern is still `LOW`.
- **Descriptive framing enforced.** Language is *"Historically, … resolved with an X% win rate
  across N trades (95% CI …)"*, tagged a hypothesis to validate live; a test asserts advice
  phrases ("you should", "will win", "take this trade", …) never appear. Only VALIDATED patterns
  become recommendations; none → run status `INSUFFICIENT_DATA` (fabricates nothing).
- **Storage foundation:** append-only migration `0009` adds `learning_recommendations` (its own
  table; no Sprint 1–3 / M1–M3 table changed). The engine is **read-only** (writes nothing yet).
  Deterministic (SHA-256 checksum), thread-safe, idempotent; duplicate identities collapsed.
  Structured logging of counts + confidence distribution — never vectors/embeddings/reasoning.
  21 tests (classifier/rubric, determinism, identity, evidence traceability, contextual
  limitations, no-advice, dedup, empty/malformed/version/missing-evidence, concurrency, migration
  + round-trip, no-writes, no-engine-imports).

**M5 — Learning REST API** (`app/api/learning.py`): a **thin transport** over M1–M4, mounted at
`/learning/*` beside `/memory/*`. It validates requests, **composes** the (pure, read-only)
pipeline — Dataset → Patterns → Statistics → Recommendations — and serialises deterministic
responses. **No analytics of its own**; imports neither engine; never writes, retrains, or
predicts.
- **Endpoints:** `GET /learning/summary` · `/patterns` · `/statistics` · `/recommendations` ·
  `/evidence/{recommendation_id}` · `GET /learning/health` · `POST /learning/run`. Patterns /
  statistics / recommendations support **filtering** (symbol / sector / timeframe / regime /
  status / category / confidence) and **deterministic pagination** (stable `pattern_key` /
  `recommendation_key` order). Every response carries a **metadata envelope** (`schema_version`,
  `learning_version`, `dataset_version`, `generated_at`) + the domain **checksums**.
- **Stateless = deterministic.** Each request re-composes the pipeline over the current corpus;
  because every stage is deterministic, concurrent identical requests return identical content
  (asserted). `POST /learning/run` is **idempotent** — a stable `run_id` per (corpus + params).
- **Error taxonomy:** 400 (bad filter/param, unknown correction), 404 (unknown recommendation),
  409 (learning/schema-version mismatch), 422 (FastAPI type/bounds), 503 (retrieval unavailable);
  full OpenAPI models. Empty corpus → honest `INSUFFICIENT_DATA` everywhere.
- **Mounting:** `app/api/main.py` gains one `include_router(learning_router)` line (the only
  change outside `app/learning/`); reuses the app-lifespan `RetrievalEngine`. Structured logging
  of endpoint + duration + status — never vectors/embeddings/reasoning. 24 tests (every endpoint,
  validation, pagination, filtering, ordering, error taxonomy, health, evidence, concurrency,
  schema-version, OpenAPI, no-engine-imports).

---

## (A) Meta-model retrainer — status: 🟡 Built, waiting on data — `app/training/meta.py`,
`app/scripts/nightly_retrain.py`, `app/training/challenger_compare.py`,
`app/training/walk_forward.py`.

## Responsibilities
- **Meta-model:** learn from the live Track Record which setups actually win → veto the
  rest. Needs ~200 resolved calls before it will train (refuses on thin data — by design).
- **Nightly retrain:** retrain on fresh data; a challenger replaces the champion **only**
  if it beats it out-of-sample by a real margin (promotion gate). Most retrains *should*
  be rejected.
- **Research engine:** every new idea → challenger → purged walk-forward → significance →
  promote or reject. No manual, undocumented experiments.

## Architecture
- `meta.py` — `MetaStatus` (readiness), `build_dataset` (reconstructs conditions for each
  resolved call), `train` (time-series CV, refuses < MIN_CALLS).
- `nightly_retrain.py` — champion vs challenger on the same holdout; **PROMOTION_MARGIN**
  (+0.5pp) + min-sample guard; backs up the champion; logs every attempt to
  `retrain_history.jsonl`.
- `challenger_compare.py` + `walk_forward.py` — the honest pipeline any feature/model idea
  runs through (uncertainty gate + class-balance gate so noise can't sneak in).

## Inputs / Outputs
- **In:** historical predictions + real outcomes (Vol 13), fresh market data.
- **Out:** a promoted (or rejected) model artifact + a logged decision & reason.

## API / ops integration
- Runs on a schedule (cron / Task Scheduler). `python -m app.training.meta --status`
  reports readiness.

## Failure / logging
- Insufficient data → refuse to train (loud, not silent). A bad retrain → rejected,
  champion untouched, attempt logged.

## Testing
- `tests/test_walk_forward.py` — the harness + the accept/reject gates (rejects noise &
  class-imbalance, accepts a real balanced gain). Meta readiness tested.

## Prediction-Model integration
- This is how the core models *improve over time* — but **only via validated promotion**.
  Never auto-promote on training accuracy; never bypass the untouched final test.

## LLM integration
- The assistant can report learning status ("the AI has logged N calls; meta-model needs
  M more") — reading, not deciding.

## Honest note
- The Learning Engine cannot manufacture an edge from nothing. It sharpens *trade
  selection* from real outcomes. It depends entirely on the Forward-Testing record
  (Vol 18) existing first.

## Future
- Drift-triggered retrains (Vol 27); per-market/per-sector meta-models; online-safe
  updates (never tick-by-tick — that learns noise).
