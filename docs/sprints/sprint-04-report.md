# Sprint 4 Report — Learning Engine (Volume 15)

- **Sprint:** 4 · Behavioural Learning Engine
- **Status:** ✅ **COMPLETE**
- **Recommended release tag:** `v0.4.0-learning-engine`
- **Version:** `app/__init__.py` → `0.4.0`
- **Repo:** `SuriyaDcruze/Ai_pred` · branch `main`

> Plan & per-milestone status: [../architecture/sprints/sprint-04-learning-plan.md](../architecture/sprints/sprint-04-learning-plan.md).
> Volume 15: [../architecture/15-learning-engine.md](../architecture/15-learning-engine.md).
> Decisions: [../architecture/adr/](../architecture/adr/) (0017–0022). Release notes:
> [../releases/v0.4.0-learning-engine.md](../releases/v0.4.0-learning-engine.md).

---

## 1. Objectives
Turn **completed** Historical Memory into **statistically honest, explainable observations** about
how past decisions actually fared — grouped by setup conditions — and package the *validated* ones
as **evidence-bound, descriptive** recommendation objects for Decision Intelligence and the GPT
assistant to explain. Built as **honesty-gated descriptive analytics — no training, no
prediction, no advice** (distinct from the legacy meta-model retrainer, ADR 0017),
**deterministically**, and **without** touching Sprint 1/2/3 or the Prediction/Outcome engines.
On today's near-empty corpus the honest output is `INSUFFICIENT_DATA` — a *pass*, not a gap.

## 2. Completed milestones
| M | Scope | Tests | Commit |
|---|---|---|---|
| M1 | Learning Dataset Builder — Historical Memory → deterministic `lds-1`/`lrn-1` dataset (read-only) | 23 | `d5e72f5` |
| M2 | Pattern Extraction — deterministic candidate patterns (metadata + evidence; `HYPOTHESIS` only) | 25 | `882eb1e` |
| M3 | Statistical Validation — Wilson CI + significance + BH/Bonferroni; reuses Sprint 2 `_metrics` | 29 | `d5d8990` |
| M4 | Recommendation Engine — evidence-bound **descriptive** objects; confidence ≠ significance | 21 | `ad753b4` |
| M5 | REST API — `/learning/*` (7 endpoints), thin transport, composed pipeline | 24 | `11ed928` |
| M6 | Documentation & freeze | — | *(this milestone)* |

**Learning Engine tests: 122.** Every milestone was plan-gated (plan → approve → implement →
review → next), each proving Sprint 1/2/3 + the engines untouched.

## 3. Architecture summary (which layers exist)
```
  Prediction / Outcome / Risk engines   🟢 built (immutable; never imported by Learning)
        │
        ▼
  FORWARD TESTING        🟢 COMPLETE (Sprint 1, v0.1.0)
        │  writes predictions
        ▼
  HISTORICAL MEMORY      🟢 COMPLETE (Sprint 2, v0.2.0) — records + reasoning + aggregates
        │  Memory Record + memory_aggregates (RetrievalEngine, read-only)
        ▼
  SIMILARITY ENGINE      🟢 COMPLETE (Sprint 3, v0.3.0) — optional neighbour context
        │
        ▼
  LEARNING DATASET       🟢 M1 — lds-1/lrn-1 (deterministic, read-only) → learning_runs
        │
        ▼
  PATTERN EXTRACTION     🟢 M2 — candidate patterns (metadata + evidence) → learning_patterns
        │
        ▼
  STATISTICAL VALIDATION 🟢 M3 — Wilson CI + significance + correction → learning_pattern_stats
        │
        ▼
  RECOMMENDATION ENGINE  🟢 M4 — evidence-bound descriptive objects → learning_recommendations
        │
        ▼
  REST API               🟢 M5 — /learning/* (summary, patterns, statistics, recommendations,
        │                        evidence, run, health)
        ▼
  DECISION INTELLIGENCE / GPT   🟡 basic (Vol 07) — can explain the observations (future)
```
Package: `app/learning/` (peer of `app/memory/`, `app/similarity/`) + `app/api/learning.py`.

## 4. Implementation summary
- **Dataset** (`models.py`, `dataset.py`): a read-only, versioned `LearningDataset` composed on
  read from completed Memory Records (realised R present); SHA-256 checksum; `INSUFFICIENT_DATA`
  below `min_corpus`. Canonical states `VALIDATED`/`HYPOTHESIS`/`INSUFFICIENT_DATA` established.
- **Patterns** (`patterns.py`): groups the dataset along an extensible dimension registry (symbol,
  sector, timeframe, regime/phase, confidence/holding bucket, model/feature version, outcome) into
  deterministic **candidate** patterns — metadata + evidence only, every one a `HYPOTHESIS`.
- **Statistics** (`statistics.py`): the first statistical milestone. Reuses the Sprint 2 aggregate
  math (`app.memory.aggregates._metrics`, regression-asserted) for base rollups, and **adds** the
  95% Wilson interval, a two-proportion significance test, an extensible multiple-comparison
  correction registry (Benjamini–Hochberg / Bonferroni / none), and a sub-period consistency score.
  Strict promotion gate → `VALIDATED` / `HYPOTHESIS` / `INSUFFICIENT_DATA`.
- **Recommendations** (`recommendations.py`): turns VALIDATED patterns into **descriptive,
  evidence-bound** objects — plain-language summary + verbatim statistical basis + supporting
  `prediction_id`s (sourced from M2 candidates) + always-non-empty limitations; a
  communication-confidence rubric **independent of significance**; no-advice framing enforced.
- **API** (`app/api/learning.py`): thin transport; seven `/learning/*` endpoints; composes the
  four engines statelessly (determinism ⇒ thread-safety); filtering + deterministic pagination;
  `400/404/409/422/503`; metadata envelope + checksums; imports neither engine.

## 5. Testing summary
- **Sprint 4 tests: 122** (M1 23 · M2 25 · M3 29 · M4 21 · M5 24).
- **Total project tests: 689 passed, 0 failed** (100% pass rate; 567 → 689, net +122).
- **Integration coverage:** end-to-end seed → build memory → dataset → patterns → validate →
  recommend → API; empty-corpus and thin-corpus `INSUFFICIENT_DATA` states first-class.
- **Reuse verification:** a regression test asserts a validated pattern's win rate / avg R /
  expectancy / profit factor / max drawdown / holding **equal** `compute_aggregates(...)` for the
  same records — the Sprint 2 math is reused, not re-implemented.
- **Isolation verification:** AST guards prove `app/learning/*` and `app/api/learning.py` import
  **neither** engine; the engine writes **nothing** (learning tables provisioned, stay empty);
  migration tests prove `predictions` (and every prior table) is byte-for-byte unchanged. All
  tests use temporary databases.
- **Deterministic verification:** identical corpus → identical dataset/validation/recommendation
  checksums; deterministic ids; `POST /learning/run` idempotent (stable `run_id`); API responses
  reproducible across sequential and concurrent calls.

## 6. Design decisions (ADRs 0017–0022)
- **0017** Behavioural Learning Engine, separate from the meta-model retrainer.
- **0018** The Learning Engine is read-only over everything upstream.
- **0019** Statistical honesty model (threshold-gated, interval-first, correction-aware).
- **0020** Recommendation philosophy: descriptive, evidence-bound, never advice.
- **0021** Learning REST API: thin transport, stateless-deterministic.
- **0022** Learning versioning & append-only satellite storage.

## 7. Verification checklist
- ✅ **Sprint 1 unchanged** — `app/forward_testing/`, `/forward/*` clean/green.
- ✅ **Sprint 2 unchanged** — `memory_aggregates` math **reused** (read-only import of `_metrics`),
  no memory table or file modified; Sprint 2 suites pass unchanged.
- ✅ **Sprint 3 unchanged** — `app/similarity/`, `/memory/similar*` clean/green.
- ✅ **Prediction Engine unchanged** — `app/ai/sklearn_model.py` never imported/modified.
- ✅ **Outcome Engine unchanged** — `app/ai/outcome_model.py` never imported/modified; the engine
  reads its **already-stored** outputs verbatim.
- ✅ **Deterministic behaviour verified** — see §5.
- ✅ **Read-only guarantee** — writes only its own (provisioned) tables; through M5 writes nothing.
- ✅ **Statistical honesty** — nothing below `min_sample`; intervals + significance + correction;
  weak evidence never `VALIDATED`.
- ✅ **Recommendation traceability** — every recommendation lists supporting `prediction_id`s +
  limitations; no advice language.
- ✅ **Migration integrity** — append-only `0006`–`0009`; every prior table byte-for-byte unchanged.

## 8. Known limitations
- **Live corpus still ~empty** — until Forward Testing accumulates a real record, the honest
  output is `INSUFFICIENT_DATA` everywhere. Every path reports sample size; the empty state is a
  first-class *pass*.
- **Recompute-on-read** — the API re-composes the pipeline per request (deterministic ⇒ consistent);
  a cached last-run is a future option at scale (ADR 0021).
- **Not yet persisting** — through M5 the engine is read-only; `POST /learning/run` reports a
  deterministic run without writing the learning tables. Persistence + a scheduled run are a future
  concern.
- **Descriptive only — no predictive edge claimed.** Consistent with `docs/RESULTS.md`: the only
  verified edge remains the Outcome Engine (backtest-only). The Learning Engine surfaces honest
  historical observations; it manufactures no edge.

## 9. Future roadmap
- **Feed the live corpus** (Sprint 1 tech debt: auto-record + monitor wiring) so the engine has
  real history to analyse; most outputs stay `INSUFFICIENT_DATA` until then.
- **Persist runs** (populate `learning_runs`/`_patterns`/`_pattern_stats`/`_recommendations`) +
  a scheduled analysis pass; a learning / decision-intelligence UI; GPT grounding on the
  evidence-bound observations (Vol 07).
- **The legacy meta-model retrainer** (`app/training/`, the *other* Vol 15 sense) remains a
  separate future concern under the model process (ADR 0002/0003) — it needs ~200 resolved calls.

---

## Sprint 4 freeze summary
- **Milestones completed:** 6 / 6 (M1–M6).
- **Modules added:** 6 — `app/learning/{models,dataset,patterns,statistics,recommendations}.py` +
  `app/api/learning.py` (plus package `__init__`).
- **Existing files touched:** 2 additively — `app/database/migrations.py` (append `0006`–`0009`),
  `app/api/main.py` (mount the learning router) — plus the `app/__init__.py` version bump.
- **Migrations:** **4** (`0006 learning_runs`, `0007 learning_patterns`, `0008
  learning_pattern_stats`, `0009 learning_recommendations`) — all own tables, no prior table
  changed.
- **API endpoints added:** 7 (`/learning/*`).
- **Tests added:** 122 → full suite **689 passed, 0 failed**.
- **Sprint 1/2/3 & engines:** provably untouched (import-guard + no-write + unchanged-tests +
  Sprint 2 aggregate-math reuse).
- **Version:** `0.3.0` → `0.4.0`.

**Definition of Done — met:** learning datasets are deterministic + versioned; statistical analysis
is reproducible with sample size + method + confidence on every figure and nothing below threshold;
recommendations are evidence-backed and descriptive (never advice/prediction); `/learning/*` is
complete thin transport; `predictions`, the engines, and Sprint 1/2/3 behaviour are provably
unchanged; Volume 15 documents the as-built Behavioural Learning Engine; Sprint 4 is frozen at
`v0.4.0` with tag `v0.4.0-learning-engine`.
