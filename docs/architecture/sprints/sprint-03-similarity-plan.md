# Sprint 3 — Similarity Engine (Volume 14) · Architecture & Implementation Plan

> **Sprint sequence:** Sprint 1 (Forward Testing, `v0.1.0`) → Sprint 2 (Historical Memory,
> `v0.2.0`) → **Sprint 3 (Similarity Engine)** — this document.
> Process (identical to Sprints 1–2): **Architecture → Sprint Plan → Milestones → Review →
> Approval → Implementation.** One milestone at a time with a review gate after each.
>
> **Status:** 🔨 In progress. **M1 (Feature Vector Builder): ✅ done — awaiting review.**
> M2–M6 pending.
>
> **Note on process:** Sprint 3 began at an M1 implementation spec (no separate plan was
> requested first). This document is written **after** M1 to give the sprint the same
> plan-doc footing as Sprints 1–2 and to lay out M2–M6 before they are built.

**Related:** Vol 14 (Similarity Engine — the north-star spec), Vol 13 (Historical Memory —
the upstream source), Vol 21 (Database Design — `memory_embeddings`),
[ADRs](../adr/) 0007/0008/0011, [Sprint 2 report](../../sprints/sprint-02-report.md).

---

## 0. Ground truth — what Sprint 2 left us

| Sprint 2 component | State | Relevance to Sprint 3 |
|---|---|---|
| `predictions` + Historical Memory satellites | ✅ built | the corpus similarity draws on |
| `RetrievalEngine` (composed Memory Records) | ✅ built | the **input** to feature vectors |
| `memory_embeddings` table (vectors `NULL`) | ✅ built | the **storage** Sprint 3 fills |
| `/memory/similar/{id}` — returns *"unavailable"* | ✅ built (contract) | the **contract** Sprint 3 will light up |
| Legacy `app/ai/similarity_engine.py` (kNN) | ✅ built | explainability only — **no predictive edge** (below) |
| **Live sample** | ⚠️ still accumulating | similarity is near-empty until Forward Testing populates memory |

**Two honesty anchors that shape the sprint:**
1. **Similarity is explainability, not a new edge.** The legacy kNN similarity was tested as a
   predictive feature on the untouched test and **added no edge** — the Outcome Engine already
   captures it (Vol 14). Sprint 3's Similarity Engine is therefore built to **explain and
   ground** ("your setup resembles N past ones that won X% at +YR"), **not** to produce a new
   trading signal, unless a fresh honest test proves otherwise. No result will be over-claimed.
2. **No inference, no training, no touching the models.** Embeddings are a **deterministic
   transform** of the feature vector — not a learned model. The Prediction/Outcome engines and
   all Sprint 1/2 code stay frozen; Similarity imports neither (ADR 0002/0003, asserted).

---

## 1. Executive Summary

The **Similarity Engine (Vol 14)** answers *"I have seen this setup before."* It converts each
Historical Memory Record into a deterministic **feature vector** (M1), stores a deterministic
**embedding** of that vector in `memory_embeddings` (M2), and retrieves the **k most similar**
historical decisions with their honest outcome stats (M3), then exposes that over the existing
`/memory/similar` contract and/or a small API (M4–M5).

**What it is:** a read/enrich layer over Historical Memory that fills the embedding placeholder
and ranks by vector distance — pure, deterministic, explainability-focused.

**What it is not:** not a predictor, not a trainer, not a model. It never modifies Historical
Memory's facts, `predictions`, or the engines; it only **adds** embeddings (its own satellite
column, already provisioned) and computes distances.

**Scope:** six milestones — feature vectors → embeddings → similarity search → retrieval
integration → API → documentation.

---

## 2. Responsibilities

### 2.1 What the Similarity Engine OWNS
- The **feature representation** (`FeatureVectorBuilder`, versioned `sim-fv-1`).
- The **embedding transform** and the population of `memory_embeddings` (via `MemoryStore`).
- The **similarity search** (distance metric, k-NN, filter-then-brute-force) and the honest
  neighbour statistics.
- The response shape behind the `/memory/similar` contract.

### 2.2 What it READS
- Historical Memory Records via `RetrievalEngine` (read-only).
- `memory_embeddings` via `MemoryStore` (read).

### 2.3 What it WRITES
- **Only** `memory_embeddings` rows (its provisioned satellite), via `MemoryStore.upsert_embedding`.
- Nothing else. Never `predictions`, never other satellites' meaning.

### 2.4 What it NEVER CHANGES
- `predictions`, the Prediction/Outcome engines, the Risk Engine, Forward Testing, the Memory
  Builder's reasoning/aggregate outputs. Model artifacts. Any prior migration.
- It performs **no** inference and **no** training.

---

## 3. Architecture

```
  RetrievalEngine ──► Memory Record ──► FeatureVectorBuilder ──► FeatureVector (sim-fv-1, dim 100)
   (Sprint 2)                                (M1 ✅)                     │
                                                                        ▼
                                                        EmbeddingGenerator (M2, deterministic)
                                                                        │  upsert
                                                                        ▼
                                                        memory_embeddings  (MemoryStore)
                                                                        │  read
                                                                        ▼
                                                        SimilaritySearch (M3, k-NN over vectors)
                                                                        │
                                                                        ▼
                                       /memory/similar contract lit up (M4–M5)  ──► GPT / dashboard
```
- **Layering:** M1 (transform) → M2 (store embeddings) → M3 (search) → M4 (wire into retrieval)
  → M5 (API) → M6 (docs). Each milestone is independently reviewable.
- **Package:** `app/similarity/` (peer of `app/memory/`).

---

## 4. Milestone Breakdown

| M | Title | Scope | Deliverables | State |
|---|---|---|---|---|
| **M1** | Feature Vector Builder | Memory Record → deterministic versioned vector (`sim-fv-1`, dim 100); encoders; typed errors | `app/similarity/{models,feature_vector}.py`; 19 tests | ✅ **done** |
| **M2** | Embedding Generator | Deterministic transform of the feature vector → `memory_embeddings` (via `MemoryStore`); idempotent backfill; **no training** | `app/similarity/embedding.py`; tests | ⏳ pending |
| **M3** | Similarity Search | Distance metric (cosine) + k-NN; **filter-then-brute-force** over a pre-filtered candidate set; honest neighbour stats (win rate, avg R, n); logged caps | `app/similarity/search.py`; tests | ⏳ pending |
| **M4** | Retrieval integration | Light up the `/memory/similar` contract by injecting an **optional** similarity engine into retrieval (additive hook); still returns "unavailable" when disabled | small additive wiring; tests | ⏳ pending |
| **M5** | API | Expose similarity results (via the existing `/memory/similar` + any `/similarity/*` needed); validation, honest sample size | `app/api` additive; API tests | ⏳ pending |
| **M6** | Documentation & freeze | Vol 14 as-built, Sprint 3 report, ADRs, release notes (`v0.3.0`) | docs | ⏳ pending |

**Design notes / decisions to confirm at each gate:**
- **Embeddings are deterministic, not learned** (M2): e.g. L2-normalised feature vector, or a
  fixed-seed random projection for dimensionality reduction — reproducible, no model artifact.
- **Lighting up `/memory/similar` (M4)** needs a small, **additive** change to Sprint 2's
  `RetrievalEngine.similar()` — an optional injected engine (default off → still "unavailable").
  This is the one deliberate, reviewed touch of a Sprint 2 file; it does **not** change existing
  behaviour when the engine is absent, and will be flagged at the M4 gate.
- **SQLite has no ANN index** — search is brute-force over a candidate set narrowed by cheap
  filters (symbol/sector/regime/timeframe), with any cap **logged**; pgvector is the scale path
  (Vol 21). This mirrors the design already documented in the Sprint 2 plan §5.3.

---

## 5. Testing Strategy
- **Unit:** deterministic vectors (M1 ✅); deterministic embeddings; distance/k-NN correctness on
  known vectors; empty-corpus behaviour; version-mismatch rejection.
- **Integration:** seed predictions → build memory → generate embeddings → similarity search
  returns the expected neighbours with correct honest stats; `predictions` + memory facts
  unchanged (no-write assertions).
- **Isolation guards (as in Sprints 1–2):** `app/similarity/*` imports neither engine (AST);
  writes only `memory_embeddings`.
- **Discipline:** temporary databases only; production data never touched.

---

## 6. Risks
| # | Risk | Mitigation |
|---|---|---|
| S1 | Similarity mistaken for a predictive edge | Framed + documented as **explainability only**; any edge claim requires a fresh untouched-test (it failed before) |
| S2 | Non-deterministic embeddings | Deterministic transform (fixed seed / normalisation); no training; version-stamped |
| S3 | Brute-force too slow at scale | Filter-then-brute-force; logged caps; pgvector path (Vol 21) |
| S4 | M4 touches frozen Sprint 2 code | Additive optional hook, default-off; behaviour unchanged when disabled; flagged at the gate |
| S5 | Near-empty memory misleads results | Empty-state tests; always report sample size; no claim below threshold |

---

## 7. Definition of Done (Sprint 3)
1. Every Memory Record encodes to a deterministic, versioned feature vector (M1 ✅).
2. Embeddings are generated and stored in `memory_embeddings` (idempotent).
3. `/memory/similar` returns the k most similar historical decisions with **honest** stats and
   sample size — or a documented "unavailable" when disabled.
4. `predictions`, the engines, and Sprint 1/2 behaviour are provably unchanged; all prior tests
   still pass.
5. Vol 14 documents the as-built engine; Sprint 3 is frozen with a release tag.

---

## Milestone status log
- **M1 (Feature Vector Builder) — ✅ done** (commit `4e3ab2c`): `sim-fv-1`, dimension **100**;
  fixed one-hot vocabularies + stable SHA-1 hashing + clamped min-max scaling + present flags;
  typed errors; 19 tests; full suite **490 passed**. Sprint 1 & 2 untouched. As-built detail in
  [Vol 14](../14-similarity-engine.md).
- M2–M6 — pending approval, one gate at a time.
