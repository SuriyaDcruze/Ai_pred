# Sprint 4 — Learning Engine (Volume 15) · Architecture & Implementation Plan

> **Planning only. No code, no SQL, no endpoints are written in this document.**
> Process (identical to Sprints 1–3): **Architecture → Sprint Plan → Milestones → Review →
> Approval → Implementation.** One milestone at a time with a review gate after each.
>
> **Status:** ✅ **SPRINT 4 COMPLETE** — frozen at `v0.4.0`, tag `v0.4.0-learning-engine`. Both §0
> reviewer decisions confirmed (separate subsystem; descriptive-only, threshold-gated).
> **M1 ✅ · M2 ✅ · M3 ✅ · M4 ✅ · M5 ✅ · M6 (docs & freeze) ✅ — all done.** See the
> [Sprint 4 report](../../sprints/sprint-04-report.md), [release notes](../../releases/v0.4.0-learning-engine.md),
> and [ADRs 0017–0022](../adr/).
>
> **Sprint sequence:** Sprint 1 (Forward Testing `v0.1.0`) → Sprint 2 (Historical Memory
> `v0.2.0`) → Sprint 3 (Similarity Engine `v0.3.0`) → **Sprint 4 (Learning Engine `v0.4.0`)**.

**Related:** Vol 15 (Learning Engine spec), Vol 13 (Historical Memory), Vol 14 (Similarity),
Vol 03/24 (SEBI posture / compliance), [ADRs](../adr/) 0002/0003/0005/0011,
[Sprint 3 report](../../sprints/sprint-03-report.md), and `docs/RESULTS.md` (the honest
scoreboard this sprint must not contradict).

---

## 0. Ground truth — and two things the reviewer must decide first

### 0.1 What Sprints 1–3 left us
| Component | State | Relevance to Sprint 4 |
|---|---|---|
| `predictions` + Historical Memory satellites | ✅ built | the completed decisions the Learning Engine analyses |
| `RetrievalEngine` (composed Memory Records, filters, aggregates) | ✅ built | the **read** surface Learning consumes |
| `memory_aggregates` (win rate, avg R, expectancy, PF, max DD by dimension/model) | ✅ built | **most of the statistics already exist** — reuse, don't reinvent (§4.4) |
| Similarity Engine (`/memory/similar*`) | ✅ built | optional neighbour context for pattern grouping |
| **Live sample** | ⚠️ **still ~empty** | **the Learning Engine will correctly output "insufficient data" everywhere until Forward Testing accumulates a real corpus** |

### 0.2 ⚠️ Scope collision — there are already two "Learning Engines" (must disambiguate)
Volume 15 **already describes a Learning Engine** — but a *different* one:

| | **(A) Legacy Vol 15 — Meta-model retrainer** | **(B) Sprint 4 — this plan** |
|---|---|---|
| Code | `app/training/meta.py`, `nightly_retrain.py`, `walk_forward.py` | new `app/learning/` package |
| What it does | **trains** a meta-model; champion/challenger **promotion** | **no training**, **no models** — statistical/behavioural analytics over history |
| Changes models? | Yes, via the validated promotion pipeline (ADR 0002/0003) | **Never.** Read-only over completed data |

The Sprint 4 spec is explicit: *"not a machine-learning model; performs no model training; no
prediction generation."* So Sprint 4 builds a **Behavioural / Statistical Learning layer**,
**distinct** from the legacy meta-model retrainer (which stays a separate, future concern under
the model process). **Recommended: name the package `app/learning/` and, in docs, call it the
"Behavioural Learning / Learning Analytics" engine** to avoid confusion with the retrainer.
*Reviewer decision #1: confirm this disambiguation (or tell me to merge/rename).*

### 0.3 ⚠️ The honesty problem this sprint MUST solve to be allowed to exist
This project's entire identity (`docs/RESULTS.md`) is: **small-sample patterns are noise.** The
earlier "high-confidence bucket looked 90% / profitable" was a *mirage* that vanished under the
real path-dependent backtester; feature engineering is *exhausted*; the Outcome Engine is the
**only** verified edge. A naïve "pattern extraction + recommendation engine" is **exactly the
machine that manufactured those fake edges.** So Sprint 4 is worth building **only** if it is
built as **honesty-gated descriptive analytics**, not an insight generator:
- **Descriptive, not prescriptive.** Outputs are *"historically, setups like X resolved Y% over
  N trades (95% CI …)"* — **observations with evidence**, never *"do X."* Recommendations are
  **hypotheses for Forward Testing to validate**, not advice (also the SEBI decision-support
  posture, Vol 03/24; R3).
- **Nothing below threshold, ever.** No pattern/stat/recommendation is emitted unless the sample
  clears a configured minimum **and** its confidence interval is meaningful. On today's empty
  corpus the honest output is **"insufficient data"** everywhere — and that is a *pass*, not a
  gap.
- **Multiple-comparisons aware.** Mining many conditions inflates false positives; the design
  must correct for this (§6) or it will re-invent the noise it's meant to expose.

*Reviewer decision #2: confirm the Learning Engine is scoped as descriptive, threshold-gated
analytics (no prescriptive advice, no new "edge" claims).* I recommend yes; without it, this
sprint risks actively harming the project's credibility.

---

## 1. Executive Summary

The **Learning Engine (Vol 15, Sprint 4 sense)** analyses **completed** Historical Memory to
surface **statistically honest, explainable observations** about how past decisions actually
fared — grouped by setup conditions — and packages the *validated* ones as **evidence-bound
recommendation objects** for Decision Intelligence and the GPT assistant to explain.

**What it is:** a deterministic, read-only **analytics** layer over Historical Memory (+ its
aggregates and, optionally, Similarity). Every number carries sample size, method, and
confidence; nothing is emitted below threshold.

**What it is not:** not a model, not a trainer, not a predictor. It never modifies predictions,
outcomes, memory, or embeddings. It manufactures **no** edge — where history is thin (i.e. now),
it says so.

**Scope:** six plan-gated milestones — dataset → patterns → statistics → recommendations → API
→ docs — each read-only, deterministic, and honesty-gated.

---

## 2. Responsibilities

### 2.1 OWNS
- The **Learning Dataset** (a deterministic, versioned view assembled from Historical Memory).
- **Pattern extraction** (grouping completed decisions by conditions) and **statistical
  validation** (win rate, expectancy, avg R, drawdown, consistency, **confidence intervals**,
  significance, multiple-comparison correction).
- **Recommendation objects** — evidence-bound, threshold-gated descriptive observations.
- Its **own storage** (learning artifacts) and their **versioning**.

### 2.2 READS (read-only)
- Historical Memory via `RetrievalEngine` (Memory Records) and `MemoryStore`
  (`memory_aggregates`); optionally the Similarity Engine for neighbour context.
- The Outcome Engine's **already-stored outputs** (verbatim, via the records) — never invokes it.

### 2.3 WRITES
- **Only** its own learning tables (via a dedicated `LearningStore`). Nothing else, ever.

### 2.4 NEVER CHANGES
- `predictions`, the Prediction/Outcome/Risk engines, Forward Testing, Historical Memory facts
  (reasoning/aggregates/embeddings), the Similarity Engine. Model artifacts. Any prior
  migration. It performs **no** training and **no** inference.

---

## 3. Architecture

### 3.1 Position (the requested chain)
```
  Forward Testing → Prediction/Outcome/Risk (immutable) → HISTORICAL MEMORY → SIMILARITY
        │  completed decisions + aggregates (read-only)
        ▼
  LEARNING ENGINE (Sprint 4)
     Dataset Builder → Pattern Extraction → Statistical Validation → Learning Repository
                                                       │
                                                       ▼
                                        Recommendation Engine (evidence-bound)
                                                       │
                                                       ▼
                                        /learning/* REST API → Decision Intelligence / GPT
```
It **consumes only completed information** and **never modifies previous stages** (R4:
read-only integration, asserted by tests as in Sprints 1–3).

### 3.2 Package
`app/learning/` (peer of `app/memory/`, `app/similarity/`) + `app/api/learning.py`.

### 3.3 Design principles (each enforced)
| Principle | How |
|---|---|
| **Deterministic + reproducible** | pure functions over the dataset; no randomness; fixed ordering; a `learning_version` stamps every artifact |
| **Statistically honest** | every stat carries `n`, method, and a confidence interval; **no output below `min_sample`**; multiple-comparison correction |
| **Explainable** | every recommendation carries supporting trade ids + stats + limitations |
| **Read-only** | reads via `RetrievalEngine`/`MemoryStore`; writes only learning tables; imports neither engine (AST guard) |
| **Versioned** | `learning_version` on runs, patterns, recommendations; a method change is a new version |

---

## 4. Data Model / Storage

Reuse **`data/prediction_history.db`** (ADR 0005) — **no new database**, **append-only
migrations**, **no change to any Sprint 1–3 table**. New satellite tables (numbers assigned at
M1; `0001`–`0005` exist today, so these are `0006`+), all **derived + rebuildable**:

- **`learning_runs`** — one analysis pass: `run_id` (PK), `learning_version`, `created_at`,
  `params_json` (min_sample, dimensions, correction), `corpus_size`, `status`.
- **`learning_patterns`** — a condition + its validated stats: `pattern_id` (PK), `run_id` (FK),
  `dimension`, `bucket`, `model_version`, `n`, `wins`, `losses`, `win_rate`, `avg_r`,
  `expectancy`, `max_drawdown_r`, `ci_low`, `ci_high`, `significance`, `passed_threshold`.
- **`learning_recommendations`** — evidence-bound observation: `rec_id` (PK), `run_id` (FK),
  `kind` (descriptive category), `subject`, `statement`, `evidence_json` (supporting
  `prediction_id`s + stats), `sample_size`, `confidence`, `expected_benefit`, `limitations`,
  `learning_version`.

Notes: keyed by `run_id` so a run is atomic and re-runnable; the **Learning Dataset (M1) is
computed on read** and only *optionally* snapshotted; aggregate math **reuses** `memory_aggregates`
where possible (§4.4). Migration tests (as in Sprint 2 M1) verify a fresh DB + a populated
Sprint‑1/2/3 DB upgrade leaving all prior tables byte‑for‑byte unchanged.

### 4.4 Reuse, don't reinvent
Sprint 2's `memory_aggregates` **already** computes win rate, avg R, expectancy, profit factor,
and max drawdown by overall/symbol/sector/timeframe/regime/confidence-bucket **and per model
version**. Sprint 4's statistical layer (M3) **reads those** and **adds** what they lack —
**confidence intervals, significance, consistency, and multiple-comparison correction** — rather
than recomputing base stats. This keeps the sprint small and avoids a second, drifting source of
truth.

---

## 5. Milestone Breakdown (plan-gated)

| M | Title | Scope | Deliverables |
|---|---|---|---|
| **M1** ✅ | Learning Dataset Builder | Deterministic, versioned `LearningDataset` from Historical Memory (read-only); `INSUFFICIENT_DATA` below `min_corpus`; canonical states; learning storage foundation. | **done:** `app/learning/{models,dataset}.py`; migration `0006 learning_runs`; 23 tests. Only `migrations.py` appended (no Sprint 1–3 table changed). |
| **M2** ✅ | Pattern Extraction | Group completed decisions by conditions into deterministic **candidate** patterns (metadata + evidence only). **Descriptive only**; `HYPOTHESIS`/`INSUFFICIENT_DATA` (never `VALIDATED`). | **done:** `app/learning/patterns.py`; migration `0007 learning_patterns`; 25 tests. Read-only; only `migrations.py` appended. |
| **M3** ✅ | Statistical Validation | For each candidate: win rate, expectancy, avg R, drawdown, consistency + **confidence interval** + **significance** + **multiple-comparison correction**; **reuse `memory_aggregates`**. Emit only patterns clearing `min_sample` **and** a meaningful CI. | **done:** `app/learning/statistics.py`; migration `0008 learning_pattern_stats`; 29 tests (644 total). Reuses the Sprint 2 `_metrics` (regression-asserted); Wilson CI + two-proportion z-test + BH/Bonferroni (extensible); `VALIDATED`/`HYPOTHESIS`/`INSUFFICIENT_DATA`. Read-only; only `migrations.py` appended. |
| **M4** ✅ | Recommendation Engine | Turn **validated** patterns into **evidence-bound descriptive** recommendation objects (supporting trades + stats + confidence + sample size + **known limitations**). **Never** unsupported advice; framed as hypotheses for Forward Testing, not actions. | **done:** `app/learning/recommendations.py`; migration `0009 learning_recommendations`; 21 tests. Read-only; evidence ids sourced from M2 candidates (M3 untouched); communication-confidence rubric independent of significance; no-advice asserted. Only `migrations.py` appended. |
| **M5** ✅ | REST API | `/learning/*` thin transport (summaries, recommendations, statistics, confidence, evidence); validation; honest sample size; 400/404/409/503 taxonomy. | **done:** `app/api/learning.py` (7 endpoints); 24 tests. Thin transport — composes M1–M4, no analytics of its own; filtering + deterministic pagination; 400/404/409/422/503; metadata envelope + checksums. Only `api/main.py` mount touched (1 line). |
| **M6** ✅ | Documentation & freeze | Vol 15 as-built (disambiguated from the retrainer), ADRs, Sprint 4 report, release notes `v0.4.0`, version bump, tag. | **done:** Vol 15 finalised (data-flow + read-only/versioning/freeze); ADRs `0017`–`0022`; [Sprint 4 report](../../sprints/sprint-04-report.md); [release notes `v0.4.0`](../../releases/v0.4.0-learning-engine.md) + compatibility matrix; `app/__init__.py` → `0.4.0`; architecture README updated; tag `v0.4.0-learning-engine`. |

Each milestone: implement only that milestone → full suite green → prove Sprints 1–3 + engines
untouched → update docs in the same commit (docs-before-push) → commit + push → **STOP for
review**.

---

## 6. Statistical honesty design (the differentiator)

This is the section that decides whether the Learning Engine helps or harms.
- **Minimum sample threshold** (`min_sample`, configurable; default aligned with the platform's
  "50–100+ resolved" honesty bar). Below it → **no** pattern/stat/recommendation; the API says
  *insufficient data* with the current `n`.
- **Confidence intervals on every rate** (e.g. Wilson interval for win rate); report the interval,
  not just the point estimate. A CI spanning a coin flip → not actionable.
- **Multiple-comparison correction.** Mining K conditions and reporting the "best" inflates false
  positives; apply a correction (e.g. Benjamini–Hochberg / Bonferroni on the significance gate)
  and **log how many hypotheses were tested**. This is the direct antidote to the historical
  90%-mirage failure mode.
- **Consistency check.** A pattern that holds only in one sub-period is flagged unstable
  (mirrors the Sprint‑1 aversion to curve-fit results).
- **Evidence mandatory.** Every recommendation lists the supporting `prediction_id`s so a human
  (or the LLM) can audit it. No black-box "insights."
- **Descriptive framing.** Language is *"historically … over N trades (CI …)"*, tagged as a
  **hypothesis to validate live**, never *"do X."*

---

## 7. API Design (`/learning/*`, design only)

Thin transport (ADR 0006/0010), mounted beside `/memory/*`. Read-only except explicit
run/rebuild ops. All responses carry sample size + method + confidence; below threshold →
`insufficient_data`.

| Method · Path | Purpose |
|---|---|
| `GET /learning/summary` | overall learning summary + corpus size + honest status |
| `GET /learning/patterns` | validated patterns (filter by dimension); each with `n`, CI, significance |
| `GET /learning/recommendations` | evidence-bound recommendations (subject, statement, evidence, limitations) |
| `GET /learning/statistics` | validated stats by dimension (built on `memory_aggregates` + CIs) |
| `GET /learning/evidence/{rec_id}` | the supporting trades + stats behind one recommendation |
| `POST /learning/run` | run an analysis pass (idempotent per corpus state); writes learning tables |
| `GET /learning/health` | engine enabled? + `learning_version` + `min_sample` + corpus size |

**Not designed, by principle:** any endpoint that predicts, trains, or emits advice below the
sample threshold.

---

## 8. Testing Strategy
- **Unit:** deterministic dataset; pattern grouping; **statistical correctness** (CIs +
  significance + correction on known series, cross-checked against `PredictionStore.statistics`/
  `memory_aggregates`); threshold gating (nothing below `min_sample`).
- **Integration:** seed predictions → build memory → run learning → patterns/recommendations
  match hand-computed expectations; `predictions` + memory + embeddings unchanged (no-write
  assertions).
- **Deterministic + statistical verification:** identical corpus → identical run output;
  small-sample corpus → **`insufficient_data`** everywhere (the honesty test).
- **Isolation guards:** `app/learning/*` and `app/api/learning.py` import neither engine (AST);
  write only learning tables. Temporary databases only.
- **Regression:** all Sprint 1–3 tests continue passing (proven each milestone).

---

## 9. Risks
| # | Risk | Mitigation |
|---|---|---|
| R1 | Small historical dataset → meaningless output | `min_sample` gate; `insufficient_data` below it; first-class empty-state tests |
| R2 | False statistical conclusions (noise as signal) | Confidence intervals + significance + **multiple-comparison correction** + consistency check; log hypotheses tested |
| R3 | Recommendations mistaken for predictions/advice | Descriptive framing + evidence + "hypothesis, validate live" tag; SEBI decision-support posture; never prescriptive |
| R4 | Changing previous-sprint behaviour | Read-only integration; writes only learning tables; import-guard + no-write + unchanged-tests |
| R5 | Duplicating / drifting from `memory_aggregates` | Reuse the Sprint 2 aggregate math; add only CIs/significance on top |
| R6 | "Learning Engine" name collides with the meta-model retrainer | Disambiguate (new `app/learning/` package; docs label it Behavioural/Analytics; retrainer stays separate) |

---

## 10. Definition of Done (Sprint 4)
1. Learning datasets are **deterministic** and versioned.
2. Statistical analysis is **reproducible**, with sample size + method + confidence on every
   figure and **nothing below threshold**.
3. Recommendations are **evidence-backed** and **descriptive** (never advice/prediction).
4. `/learning/*` REST endpoints are complete (thin transport).
5. Documentation updated; Sprint 1–3 tests continue passing; Sprints 1–3 + engines provably
   unchanged.
6. Sprint 4 frozen with tag `v0.4.0-learning-engine`.

---

## 11. Estimated Scope
~5–6 new modules (`app/learning/{models,dataset,patterns,statistics,recommendations}.py` +
`app/api/learning.py`); **2** existing files touched additively (`migrations.py` append,
`api/main.py` mount); ~3 new migrations (learning tables only); ~110–150 new tests. Complexity
concentrated in **M3** (statistical validity) and **M4** (honest recommendation framing).

**Out of scope:** any model training or retraining (that is the legacy Vol 15 retrainer, under
the model process, ADR 0002/0003); any prediction/inference; Decision Intelligence logic; a
learning dashboard; Postgres migration.

---

## Deliverables checklist (this document = planning only)
1. ✅ Sprint 4 architecture · 2. ✅ Position/responsibilities · 3. ✅ Data model / storage ·
4. ✅ Milestone specifications · 5. ✅ Statistical-honesty design · 6. ✅ API design ·
7. ✅ Testing strategy · 8. ✅ Risks · 9. ✅ Definition of Done · 10. ✅ Estimated scope.
> Vol 15 as-built, ADRs, Sprint 4 report, and release notes are produced **as milestones land**
> (M6) — not in this planning doc.
11. ⏳ **Awaiting approval — plus the two reviewer decisions in §0.2 and §0.3.** No
implementation (not even M1) begins until this plan is approved.
