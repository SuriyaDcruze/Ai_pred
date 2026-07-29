# Sprint 5 — Decision Intelligence Engine (Volume 08/07 sense) · Architecture & Implementation Plan

> **Planning only. No code, no SQL, no endpoints, no migrations, no tests are written in this
> document.** Process (identical to Sprints 1–4): **Architecture → Sprint Plan → Milestones →
> Review → Approval → Implementation.** One milestone at a time with a review gate after each.
>
> **Status:** ⏳ **Awaiting architecture review + approval.** No implementation (not even M1)
> begins until this plan is approved and the two §0 reviewer decisions are confirmed.
>
> **Sprint sequence:** Sprint 1 (Forward Testing `v0.1.0`) → Sprint 2 (Historical Memory
> `v0.2.0`) → Sprint 3 (Similarity Engine `v0.3.0`) → Sprint 4 (Learning Engine `v0.4.0`) →
> **Sprint 5 (Decision Intelligence `v0.5.0`, proposed)**.

**Related:** Vol 07 (GPT/Conversation assistant), Vol 08 (Market Intelligence), Vol 13 (Historical
Memory), Vol 14 (Similarity), Vol 15 (Learning Engine), Vol 03/24 (SEBI posture / compliance),
[ADRs](../adr/) 0002/0003/0005/0006/0018/0020, [Sprint 4 report](../../sprints/sprint-04-report.md),
and `docs/RESULTS.md` (the honest scoreboard this sprint must not contradict).

---

## 0. Ground truth — and two things the reviewer must decide first

### 0.1 What Sprints 1–4 left us
| Component | State | Relevance to Sprint 5 |
|---|---|---|
| `predictions` (Prediction + Outcome + Risk outputs, stored verbatim) | ✅ built | the **decision** Sprint 5 explains — read from the store, never recomputed |
| Historical Memory (`RetrievalEngine`, Memory Records, `memory_aggregates`) | ✅ built | the **context** (reasoning, rollups) a decision is placed against |
| Similarity Engine (`/memory/similar*`) | ✅ built | the **"seen this before"** neighbours + honest outcome stats |
| Behavioural Learning Engine (`/learning/*`, patterns/stats/recommendations) | ✅ built | the **validated observations** a decision is weighed against |
| **Live sample** | ⚠️ **still ~empty** | **every Memory/Similarity/Learning input is `INSUFFICIENT_DATA` until Forward Testing accumulates a real corpus — Sprint 5 must degrade gracefully to "prediction-only"** |

**The gap Sprint 5 fills.** Sprints 1–4 built four *independent* read surfaces. Nothing yet
**composes** them into a single, explainable answer to *"what does the whole system think about
this decision, and why?"* Today a caller must hit `/forward/*`, `/memory/*`, `/memory/similar*`,
and `/learning/*` separately and stitch the story together. Sprint 5 is that composition + serving
layer — the node the Sprint 4 plan repeatedly named ("for **Decision Intelligence** and the GPT
assistant to explain").

### 0.2 ⚠️ Scope collision — intelligence surfaces already exist (must disambiguate)
The word "intelligence" is already used by **legacy, pre-Memory** modules:

| | **(A) Legacy explainable intelligence** | **(B) Sprint 5 — this plan** |
|---|---|---|
| Code | `app/intelligence.py` (V3), `app/sector.py`, Vol 08 market intelligence | new `app/decision_intelligence/` package |
| What it does | per-symbol explainable view built from **live market data + the models directly** | **composes the four sprint engines' stored/derived outputs** into one decision object |
| Built when | before Historical Memory / Similarity / Learning existed | after Sprints 1–4 — it consumes them |
| Changes models? | reads models live (analysis path) | **Never.** Read-only over already-stored/derived data |

The two are **not** the same: the legacy layer *computes* a fresh analysis from market data; Sprint 5
*composes* what the four engines already produced, adding traceability and honesty gates over the
**historical** picture. **Recommended:** name the package `app/decision_intelligence/` and, in docs,
call it the **Decision Intelligence Engine**; the legacy `app/intelligence.py` stays a separate
live-analysis concern (a later sprint may supersede it — never a destructive edit).
*Reviewer decision #1: confirm this disambiguation + the package name, and the API route ownership
(a single owner, Sprint 3 precedent — see §5/§7-M5).* 

### 0.3 ⚠️ The dependency problem this sprint must respect
Decision Intelligence's value is **gated on the corpus** (`docs/RESULTS.md`): the only verified edge
is the Outcome Engine (backtest-only), and Memory/Similarity/Learning are **near-empty in
production**. So on today's data, a composed decision object is mostly *"prediction + outcome
verdict, with `INSUFFICIENT_DATA` for historical context."* That is the **honest** output, not a
failure — and Sprint 5 must **degrade gracefully**, never fabricate context, and never invent a new
edge or a prescriptive signal (it composes, it does not predict). *Reviewer decision #2: confirm
Sprint 5 is scoped as a **read-only, honesty-gated composition/serving layer** (graceful degradation
to prediction-only; descriptive not prescriptive; no new "edge" claim), and that **feeding the live
corpus** (auto-record + monitor wiring — Sprint 1 tech debt) is a **separate, parallel
productionization track** the reviewer prioritises alongside (recommended), not folded into this
sprint.* I recommend yes to both; without them Sprint 5 risks either duplicating the legacy
intelligence or over-promising on empty data.

---

## 1. Executive Summary

The **Decision Intelligence Engine (Sprint 5)** is a deterministic, **read-only composition layer**
that assembles — for a given prediction (or symbol) — a single **explainable, evidence-bound
Decision Intelligence object**: the stored Prediction/Outcome/Risk verdict, its Historical Memory
context, its Similarity neighbours (with honest outcome stats), and the Learning Engine's validated
observations/recommendations — each element **traceable to its source** and each figure carrying its
sample size and confidence. It serves that object over a thin `/intelligence/*`-style API for the
dashboard and the (future) GPT assistant to explain.

**What it is:** the **synthesis + serving** node after the four engines — it *composes* their
already-produced outputs, adds a traceability/evidence graph and a descriptive For/Against
narrative, and gates everything on honesty (sample size, confidence, `INSUFFICIENT_DATA`).

**What it is not:** not a model, not a trainer, not a predictor; it **re-runs nothing** and
**recomputes no statistics** (it reuses each engine verbatim — the anti-duplication guarantee). It
never modifies predictions, memory, embeddings, learning artifacts, or models. It manufactures **no**
edge and emits **no** advice — where history is thin (i.e. now), it says so and falls back to the
prediction-only view.

**Scope:** six plan-gated milestones — domain/contract → composition engine → evidence &
explanation → confidence & prioritisation → API → docs — each read-only, deterministic, and
honesty-gated.

---

## 2. Responsibilities

### 2.1 OWNS
- The **Decision Intelligence object** — a deterministic, versioned composition assembled on read
  from the four sprint engines (identity, the composed verdict, the evidence graph, the descriptive
  narrative, a composite communication-confidence, and honest status).
- The **composition contract** (which engines contribute which fields, and how absence/thin data is
  represented) and the **traceability graph** (every element → its source `prediction_id` /
  `pattern_key` / `recommendation_id` / neighbour id).
- Its **own** (optional) audit/snapshot storage and its **versioning**.

### 2.2 READS (read-only)
- The **stored** Prediction/Outcome/Risk outputs via `PredictionStore` (verbatim — never invokes the
  engines).
- Historical Memory via `RetrievalEngine`/`MemoryStore` (Memory Records + `memory_aggregates`).
- The Similarity Engine (neighbours + honest summary) and the Learning Engine (patterns, statistics,
  recommendations) via their **existing** read APIs/objects.

### 2.3 WRITES
- **Only** its own (optional) decision-intelligence audit/snapshot table (via a dedicated store), if
  the reviewer approves persistence. Nothing else, ever. Default posture: **compose-on-read, write
  nothing** (Sprint 3 precedent).

### 2.4 NEVER CHANGES
- `predictions`, the Prediction/Outcome/Risk engines, Forward Testing, Historical Memory facts,
  the Similarity Engine, the Learning Engine's artifacts, model artifacts, any prior migration. It
  performs **no** training, **no** inference, and **no** recomputation of another engine's numbers.

### 2.5 Read-only guarantees (enforced, as in Sprints 2–4)
- Imports **neither** the Prediction nor the Outcome engine (AST import-guard).
- Writes **only** its own table (if any) — asserted by no-write tests over every other table.
- Every prior sprint's tests continue to pass, proven each milestone.

---

## 3. Architecture

### 3.1 Position (the requested chain)
```
  FORWARD TESTING (Sprint 1)   🟢  writes predictions
        │
        ▼
  HISTORICAL MEMORY (Sprint 2) 🟢  Memory Records + memory_aggregates
        │
        ▼
  SIMILARITY ENGINE (Sprint 3) 🟢  neighbours + honest outcome stats
        │
        ▼
  LEARNING ENGINE (Sprint 4)   🟢  patterns · statistics · recommendations
        │   (all four are read-only surfaces; none imports Prediction/Outcome)
        ▼
  ┌──────────────────────  DECISION INTELLIGENCE ENGINE (Sprint 5) ─────────────────────┐
  │  Composition Engine  →  Evidence & Explanation graph  →  Composite confidence         │
  │     reads each engine's stored/derived output verbatim; recomputes nothing            │
  └───────────────────────────────────────────┬───────────────────────────────────────────┘
                                               ▼
                     /intelligence/* REST API (thin transport, read-only)
                                               ▼
        Dashboard  +  Decision Intelligence UI  +  GPT / Conversation assistant (Vol 07, FUTURE)
```
It **consumes only completed/stored information** and **never modifies previous stages** (read-only
integration, asserted by tests as in Sprints 2–4). Future components (a Decision Intelligence UI,
the GPT orchestrator in Vol 07, Portfolio Intelligence in Vol 11) consume Sprint 5's object; they
are **out of scope** here.

### 3.2 Package
`app/decision_intelligence/` (peer of `app/memory/`, `app/similarity/`, `app/learning/`) +
`app/api/intelligence.py` (name/route ownership confirmed by reviewer decision #1). No change to any
existing package beyond an additive router mount.

### 3.3 Design principles (each enforced)
| Principle | How |
|---|---|
| **Deterministic + reproducible** | pure composition over stored inputs; no randomness; fixed ordering; a `decision_intelligence_version` stamps every object; a checksum proves same inputs → same object |
| **Explainable + traceable** | every composed element carries its source id(s); a descriptive For/Against narrative; no black-box synthesis |
| **Honesty-gated** | every figure keeps its sample size + confidence; thin/absent inputs → `INSUFFICIENT_DATA` for that facet; graceful degradation to prediction-only; **no new edge, no advice** |
| **Reuse, never recompute** | reads each engine's existing output verbatim; recomputes no statistic (anti-duplication); a change to an upstream method is a new upstream version, not a re-implementation here |
| **Read-only** | reads via the existing stores/engines; writes only its own (optional) table; imports neither the Prediction nor the Outcome engine (AST guard) |
| **Versioned** | `decision_intelligence_version` on every object; records the upstream versions it composed (prediction/outcome/feature/similarity/learning) for staleness detection |
| **Thread-safe + scalable** | stateless composition (pure over inputs); safe for concurrent requests; bounded work per object; degrades to prediction-only cheaply on a thin corpus |
| **Compliance (SEBI)** | descriptive decision-support, never advice; persistent disclaimers; every shown decision is auditable to its evidence (Vol 03/24) |

---

## 4. Storage

Reuse **`data/prediction_history.db`** (ADR 0005) — **no new database**, **append-only migrations**,
**no change to any Sprint 1–4 table**.

- **Default: compose-on-read, no new table** (Sprint 3 precedent — it added zero migrations). The
  Decision Intelligence object is **computed on read** from the four engines; nothing is persisted.
  This keeps determinism trivial (recompute == cache) and the write-surface empty.
- **Optional (reviewer-approved): one append-only audit/snapshot table**, e.g.
  `decision_intelligence_runs` (metadata + the composed object's checksum + the upstream versions it
  composed), for auditability/reproducibility of *what was shown when*. **Metadata + evidence
  references only** — never a duplicate of predictions/memory/learning rows. Number assigned at
  milestone time (`0006`–`0009` exist, so this would be `0010`). Derived + rebuildable.

**Versioning strategy.** A single `decision_intelligence_version` (e.g. `di-1`) stamps the object;
the object also records the **upstream** versions it composed (`prediction_model_version`,
`outcome_model_version`, `feature_version`, `learning_version`, `dataset_version`, similarity
`embedding_version`) so a consumer can detect when a stored/snapshotted object is stale. A method
change is a new `di-1`→`di-2`, never an edit.

**Database impact / append-only guarantees.** Zero-to-one new migration, additive only; no Sprint
1–4 table is altered; migration tests (as in Sprints 2–4) would verify a fresh DB **and** a populated
Sprint-1/2/3/4 DB upgrade leaving every prior table byte-for-byte unchanged.

*(No schema is written in this document — the table shape, if approved, is designed at its milestone.)*

---

## 5. Integration

| Subsystem | Interaction | Boundary |
|---|---|---|
| **Prediction Engine** | reads its **stored** direction/probability/confidence via `PredictionStore` | never imported, never invoked (ADR 0002/0018) |
| **Outcome Engine** | reads its **stored** target-before-stop verdict (the verified edge) verbatim | never imported, never invoked (ADR 0003/0018) |
| **Risk Engine** | reads the stored entry/stop/targets/R plan | read-only |
| **Historical Memory** | `RetrievalEngine` (Memory Record) + `MemoryStore` (`memory_aggregates`) | read-only; no memory fact changed |
| **Similarity Engine** | neighbours + honest summary via its existing read path | read-only; no embedding changed |
| **Learning Engine** | validated patterns / statistics / recommendations via its existing objects | read-only; no learning artifact changed |
| **Decision Intelligence (this)** | **produces** the composed object | its own owner |
| **GPT / Conversation (Vol 07, FUTURE)** | **consumes** the object to explain it in natural language | Sprint 5 provides structure; the GPT layer is a later sprint — the LLM never predicts |
| **Dashboard / DI UI (FUTURE)** | renders the composed decision + evidence | consumer only |

Sprint 5 is the **seam** between the four engines and the explanation/serving layer — it depends on
all of them and is depended on by none of them (no cycles).

---

## 6. Design Principles
(Enumerated as required — each is enforced by tests, mirroring Sprints 2–4.)
- **Deterministic behaviour** — pure composition; identical stored inputs → identical object.
- **Reproducibility** — a SHA-256 checksum over the ordered composed object; stamped versions.
- **Explainability** — a descriptive For/Against narrative assembled from the engines' own honest
  statements; never a black box.
- **Traceability** — every element references its originating `prediction_id` / `pattern_key` /
  `recommendation_id` / neighbour id; the object is fully auditable.
- **Versioning** — `decision_intelligence_version` + recorded upstream versions for staleness.
- **Thread safety** — stateless, pure-over-inputs composition; concurrent requests consistent.
- **Scalability** — bounded work per object; degrades cheaply to prediction-only when context is
  thin; a future snapshot/cache path is available without changing the contract.
- **Enterprise architecture** — clean module boundary (a peer package + a thin router); no
  duplicated responsibility; extensible composition registry (add a contributor without breaking
  existing consumers).
- **Compliance** — descriptive decision-support, never advice; disclaimers; audit trail (Vol 03/24).
- **Read-only boundaries** — imports neither engine; writes only its own (optional) table.

---

## 7. Milestone Breakdown (plan-gated)

| M | Title | Objective | Responsibilities / Deliverables | Dependencies |
|---|---|---|---|---|
| **M1** | Domain model & composition contract | Define the deterministic, versioned **Decision Intelligence object** + canonical states + the contract for which engine contributes which facet and how absence/thin data is represented. | Domain models + version stamps + `INSUFFICIENT_DATA`/degradation semantics; the (optional) storage foundation decision. **No composition logic yet.** | Sprints 1–4 read surfaces |
| **M2** | Composition Engine | **Assemble** the object for a prediction (or symbol) by reading each engine's existing output verbatim — prediction/outcome verdict + memory context + similarity neighbours + learning observations — deterministically, honesty-gated, recomputing nothing. | The read-only composition engine; graceful degradation to prediction-only; determinism (checksum). | M1 |
| **M3** | Evidence & Explanation | Build the **traceability graph** (every element → its source ids) and a **descriptive For/Against** narrative assembled from the engines' honest statements. **Descriptive only — never advice.** | Evidence graph + narrative generator; auditability guarantees; no-advice framing. | M2 |
| **M4** | Composite confidence & prioritisation | A deterministic, **evidence-bound** composite confidence (how strong/consistent the composed picture is — **not** a new prediction, cf. Learning's communication-confidence) and a stable ordering/prioritisation across decisions. Optional append-only snapshot store. | Composite-confidence rubric (from sample sizes + agreement + CI widths) + deterministic ordering; optional `0010` audit table. | M3 |
| **M5** | REST API | `/intelligence/*` **thin transport** — the composed object for a prediction/symbol, its evidence, health; validation; honest sample size; `400/404/409/422/503` taxonomy; single route owner (disambiguated from the legacy `GET /intelligence`). | `app/api/intelligence.py`; router mount; OpenAPI. | M2–M4 |
| **M6** | Documentation & freeze | As-built volume, ADRs, Sprint 5 report, release notes `v0.5.0`, version bump, tag `v0.5.0-decision-intelligence`. | docs + freeze. | M1–M5 |

Each milestone: implement only that milestone → full suite green → prove Sprints 1–4 + engines
untouched → update docs in the same commit (docs-before-push) → commit + push → **STOP for review**.

---

## 8. Risks
| # | Risk | Mitigation |
|---|---|---|
| R1 | **Duplicating the legacy intelligence** (`app/intelligence.py`, Vol 08) | Disambiguate (reviewer decision #1); Sprint 5 **composes** stored engine outputs, it does not re-run market analysis; new package + single API owner |
| R2 | **Over-promising on an empty corpus** | Graceful degradation to prediction-only; every facet carries sample size + `INSUFFICIENT_DATA`; no new edge/advice claim; consistent with `docs/RESULTS.md` |
| R3 | **Recomputing another engine's numbers (drift)** | Reuse each engine's output **verbatim**; recompute nothing; assert composed figures equal the source engines' figures |
| R4 | **Changing previous-sprint behaviour** | Read-only integration; writes only its own (optional) table; import-guard + no-write + unchanged-Sprint-1–4 tests |
| R5 | **Prescriptive drift / SEBI exposure** | Descriptive For/Against only; disclaimers; audit trail; composite confidence ≠ a signal; never "do X" |
| R6 | **API route collision with legacy `/intelligence`** | Single route owner + static-before-catch-all discipline (Sprint 3 / ADR 0016 precedent); reviewer confirms the prefix at M5 |
| R7 | **Non-determinism from timestamps/ordering** | Volatile fields excluded from the checksum; fixed ordering; deterministic ids |

---

## 9. Testing Strategy (high-level only — no test code here)
- **Unit:** deterministic composition (identical stored inputs → identical object + checksum);
  correct degradation (thin corpus → prediction-only + `INSUFFICIENT_DATA` facets); traceability
  (every element resolves to a real source id); composite-confidence rubric.
- **Integration:** seed predictions → build memory → embeddings → learning → **compose** → the
  object matches hand-composed expectations; `predictions` + memory + embeddings + learning artifacts
  unchanged (no-write assertions).
- **Reuse verification:** the composed figures **equal** the source engines' figures (no drift).
- **Isolation guards:** `app/decision_intelligence/*` and `app/api/intelligence.py` import **neither**
  engine (AST); write only their own (optional) table; temporary databases only.
- **API:** every endpoint, validation, filtering/pagination, error taxonomy, health, evidence,
  concurrency, schema-version, OpenAPI, no-engine-import.
- **Regression:** all Sprint 1–4 tests continue passing (proven each milestone).

---

## 10. Definition of Done (Sprint 5)
1. The Decision Intelligence object is **deterministic**, versioned, and reproducible (checksum).
2. It **composes** all four engines with **no recomputation** and **no duplication**; composed
   figures equal their sources.
3. It is **explainable + fully traceable** (every element → its source id) and **descriptive**
   (never advice/prediction); it **degrades gracefully** to prediction-only and reports
   `INSUFFICIENT_DATA` honestly on a thin corpus.
4. `/intelligence/*` is complete **thin transport** (read-only) with the standard error taxonomy.
5. Documentation updated; Sprint 1–4 tests continue passing; Sprints 1–4 + engines **provably
   unchanged** (import-guard + no-write + unchanged-tests).
6. Sprint 5 frozen with tag `v0.5.0-decision-intelligence`.

---

## 11. Estimated Scope
- **Modules:** ~3–5 new — `app/decision_intelligence/{models,compose,evidence,confidence}.py` +
  `app/api/intelligence.py` (plus package `__init__`).
- **Packages:** 1 new (`app/decision_intelligence/`).
- **Migrations:** **0–1** — compose-on-read by default; at most one append-only audit/snapshot table
  (`0010`) if persistence is approved. **No Sprint 1–4 table changed.**
- **Existing files touched:** **1** additively — `app/api/main.py` (mount the router) — plus the
  `app/__init__.py` version bump at M6.
- **Documentation:** as-built volume, ADRs (~4–6), Sprint 5 report, release notes `v0.5.0`,
  compatibility-matrix update.
- **Testing effort:** ~90–130 new tests (composition determinism, degradation, traceability,
  reuse-no-drift, API, isolation). Complexity concentrated in **M2** (correct, drift-free
  composition) and **M3** (honest, descriptive explanation).

**Out of scope:** any prediction/inference or model training; the GPT/Conversation orchestrator
(Vol 07 — a later sprint); Portfolio Intelligence (Vol 11); a Decision Intelligence **UI**; feeding
the live corpus (auto-record + monitor — a separate productionization track, reviewer decision #2);
Postgres migration; superseding/removing the legacy `app/intelligence.py`.

---

## 12. Deliverables (what Sprint 5 will produce)
1. This architecture plan (planning only).
2. `app/decision_intelligence/` — the composition engine + domain models + evidence/confidence.
3. `app/api/intelligence.py` — the thin `/intelligence/*` REST transport (+ one mount line).
4. At most one append-only migration (`0010`, audit/snapshot) — **only if** persistence is approved.
5. ADRs for Sprint 5 (composition/read-only; reuse-no-recompute; explanation philosophy; REST API;
   versioning) — numbers assigned at M6.
6. As-built volume documentation (Decision Intelligence, disambiguated from the legacy intelligence).
7. Sprint 5 report (`docs/sprints/sprint-05-report.md`).
8. Release notes `docs/releases/v0.5.0-decision-intelligence.md` + compatibility-matrix update.
9. Version bump `app/__init__.py` → `0.5.0`; freeze tag `v0.5.0-decision-intelligence`.
10. Full regression green each milestone; Sprints 1–4 provably unchanged.

---

## Deliverables checklist (this document = planning only)
1. ✅ Executive summary · 2. ✅ Responsibilities · 3. ✅ Architecture + diagram · 4. ✅ Storage ·
5. ✅ Integration · 6. ✅ Design principles · 7. ✅ Milestone breakdown · 8. ✅ Risks ·
9. ✅ Testing strategy · 10. ✅ Definition of Done · 11. ✅ Estimated scope · 12. ✅ Deliverables.
> As-built volume, ADRs, Sprint 5 report, and release notes are produced **as milestones land** (M6)
> — not in this planning doc.
13. ⏳ **Awaiting approval — plus the two reviewer decisions in §0.2 and §0.3.** No implementation
(not even M1) begins until this plan is approved.
