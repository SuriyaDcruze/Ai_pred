# Sprint 3 Report — Similarity Engine (Volume 14)

- **Sprint:** 3 · Similarity Engine
- **Status:** ✅ **COMPLETE**
- **Recommended release tag:** `v0.3.0-similarity-engine`
- **Version:** `app/__init__.py` → `0.3.0`
- **Repo:** `SuriyaDcruze/Ai_pred` · branch `main`

> Plan & per-milestone status: [../architecture/sprints/sprint-03-similarity-plan.md](../architecture/sprints/sprint-03-similarity-plan.md).
> Volume 14: [../architecture/14-similarity-engine.md](../architecture/14-similarity-engine.md).
> Decisions: [../architecture/adr/](../architecture/adr/) (0012–0016). Release notes:
> [../releases/v0.3.0-similarity-engine.md](../releases/v0.3.0-similarity-engine.md).

---

## 1. Objectives
Answer *"I have seen this setup before"* — retrieve the historical decisions most similar to a
given one, with **honest** outcome statistics, by filling the `memory_embeddings` placeholder
and lighting up the `/memory/similar` contract. Built as **explainability, not a predictive
edge** (the legacy kNN similarity added none), **deterministically**, and **without** touching
Sprint 1/2 or the Prediction/Outcome engines.

## 2. Completed milestones
| M | Scope | Tests | Commit |
|---|---|---|---|
| M1 | Feature Vector Builder — Memory Record → deterministic `sim-fv-1` vector (dim 100) | 19 | `4e3ab2c` |
| M2 | Embedding Generator — L2-normalised `sim-emb-1`; store + backfill (no training) | 21 | `a99976e` |
| M3 | Similarity Search — cosine k-NN, filter-first, honest stats, read-only | 22 | `d2363cf` |
| M4 | Retrieval integration — optional DI into `RetrievalEngine`; graceful fallback | 17 | `10597ce` |
| M5 | REST API — `/memory/similar*` (4 endpoints), engine wired into the app | 19 | `87155ea` |
| M6 | Documentation & freeze | — | *(this milestone)* |

**Similarity Engine tests: 98.** Every milestone was plan-gated (plan → approve → implement →
review → next), each proving Sprint 1/2 + the engines untouched.

## 3. Architecture summary (which layers exist)
```
  Prediction / Outcome / Risk engines   🟢 built (immutable; never imported by Similarity)
        │
        ▼
  FORWARD TESTING        🟢 COMPLETE (Sprint 1, v0.1.0)
        │  writes predictions
        ▼
  HISTORICAL MEMORY      🟢 COMPLETE (Sprint 2, v0.2.0) — records + reasoning + aggregates
        │  Memory Record (RetrievalEngine)
        ▼
  FEATURE VECTOR BUILDER 🟢 M1 — sim-fv-1, dim 100 (deterministic)
        │
        ▼
  EMBEDDING GENERATOR    🟢 M2 — sim-emb-1 (L2-normalised) → memory_embeddings
        │
        ▼
  SIMILARITY SEARCH      🟢 M3 — sim-search-1 cosine k-NN (filter-first, honest stats)
        │  injected (DI)
        ▼
  RETRIEVAL ENGINE       🟢 M4 — /memory/similar contract activated
        │
        ▼
  REST API               🟢 M5 — /memory/similar* (by-id, query, POST, health)
        │
        ▼
  GPT ASSISTANT          🟡 basic (Vol 07) — can ground on similar setups (future)
```
Package: `app/similarity/` (peer of `app/memory/`) + `app/api/similarity.py`.

## 4. Implementation summary
- **Feature vectors** (`feature_vector.py`, `models.py`): fixed one-hot vocabularies + stable
  SHA-1 hashing + clamped min-max scaling + present flags; immutable ordered layout; `sim-fv-1`
  → **100 dims**.
- **Embeddings** (`embedding.py`): L2-normalised feature vector (`sim-emb-1`); `generate` /
  `store` / `rebuild` / `backfill` (idempotent); versions packed in `model_name`; fills the
  Memory-Builder placeholder.
- **Search** (`search.py`): cosine (`cosine_similarity`); filter-first → brute-force →
  threshold → deterministic top-k; `SimilaritySummary`; logged candidate cap; `search_by_
  prediction` + `search(embedding)`.
- **Integration** (`app/memory/retrieval.py`, additive): optional engine injected via a setter
  (no import cycle); `similar()` / `similar_by_embedding()`; graceful fallback.
- **API** (`app/api/similarity.py`): thin transport; four `/memory/similar*` routes; engine
  wired in the lifespan; 400/404/409/503 taxonomy; no raw vectors exposed.

## 5. Testing summary
- **Sprint 3 tests: 98** (M1 19 · M2 21 · M3 22 · M4 17 · M5 19).
- **Total project tests: 567 passed, 0 failed** (100% pass rate).
- **Integration coverage:** end-to-end seed → build memory → generate embedding → search →
  retrieval delegation → API; empty-corpus and near-empty states first-class.
- **Isolation verification:** AST guards prove `app/similarity/*` and `app/api/similarity.py`
  import **neither** engine; Similarity writes **only** `memory_embeddings`; retrieval has **no
  module-level** `app.similarity` import (no cycle). All tests use temporary databases.
- **Deterministic verification:** identical feature vector → identical embedding (bit-for-bit);
  cosine values exact (1 / 0 / −1); ranking + tie-break reproducible; API responses
  deterministic across calls.

## 6. Design decisions (ADRs 0012–0016)
- **0012** Deterministic, versioned feature vectors.
- **0013** Deterministic embeddings (no training).
- **0014** Similarity Engine architecture (filter-first brute-force cosine).
- **0015** Retrieval integration via optional dependency injection.
- **0016** Similarity REST API (thin transport, single route owner).

## 7. Verification checklist
- ✅ **Sprint 1 unchanged** — `app/forward_testing/`, `/forward/*` clean/green.
- ✅ **Sprint 2 unchanged** — only `retrieval.py` touched (additively, M4); Sprint 2's 47
  retrieval/API tests pass unchanged; `predictions` schema & data untouched.
- ✅ **Prediction Engine unchanged** — `app/ai/sklearn_model.py` never imported/modified.
- ✅ **Outcome Engine unchanged** — `app/ai/outcome_model.py` never imported/modified.
- ✅ **Deterministic behaviour verified** — see §5.
- ✅ **No training performed** — embeddings are a normalisation, not a learned model.
- ✅ **No inference introduced** — the Similarity Engine computes distances, never predictions.

## 8. Known limitations
- **Brute-force search** over a candidate set (SQLite has no ANN index) — bounded by a logged
  candidate cap.
- **SQLite scalability** — single-file, single-writer; fine at current volumes.
- **pgvector future migration** — the schema shape is chosen so this is a migration, not a
  redesign (Vol 21).
- **Live corpus still growing** — memory is near-empty in production until Forward Testing
  populates it (Sprint 1 tech debt: auto-record + monitor wiring). Every path reports sample
  size and handles the empty state.
- **Explainability only — no predictive edge claimed.** The legacy kNN similarity added no edge
  on the untouched test; any future edge claim requires a fresh honest test.

## 9. Future roadmap — Sprint 4 (Learning Engine, Vol 15)
Planned (no implementation yet):
- **M1** Training dataset builder — assemble the labelled history (Memory Records + realised R)
  the Learning Engine trains on, deterministically and leakage-safe.
- **M2** Meta-model training harness — champion/challenger over the honest pipeline; the
  Prediction/Outcome engines change **only** through this deliberate process (ADR 0002/0003).
- **M3** Evaluation + promotion — purged walk-forward, untouched-test gates, calibration.
- **M4** Registry integration — version + lineage of any promoted model.
- **M5** API + reporting; **M6** docs & freeze.
Plus: feed the live corpus (auto-record + monitor), pgvector when scale demands, and a
similarity/decision-intelligence UI.

---

## Sprint 3 freeze summary
- **Milestones completed:** 6 / 6 (M1–M6).
- **Modules added:** 5 — `app/similarity/{models,feature_vector,embedding,search}.py` +
  `app/api/similarity.py` (plus package `__init__`).
- **Existing files touched:** 3 additively — `app/memory/retrieval.py` (M4 DI),
  `app/api/memory.py` (route moved out), `app/api/main.py` (lifespan wiring).
- **Migrations:** **0** (Sprint 3 adds no schema — it fills the `memory_embeddings` provisioned
  in Sprint 2).
- **API endpoints added:** 4 (`/memory/similar*`).
- **Tests added:** 98 (net +96 after moving 2) → full suite **567 passed, 0 failed**.
- **Sprint 1/2 & engines:** provably untouched (import-guard + no-write + unchanged-tests).
- **Version:** `0.2.0` → `0.3.0`.

**Definition of Done — met:** every Memory Record encodes to a deterministic versioned vector;
embeddings are generated + stored idempotently; `/memory/similar` returns the k most similar
decisions with honest stats + sample size (or *unavailable* when disabled); `predictions`, the
engines, and Sprint 1/2 behaviour are provably unchanged; Vol 14 documents the as-built engine.
