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
