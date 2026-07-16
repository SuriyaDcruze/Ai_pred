# Volume 15 — Learning Engine

## Purpose
Turn accumulated experience into (validated) improvement — the **meta-model** that learns
which signals win, and the **nightly champion/challenger retrain** that only promotes a
model that genuinely beats the incumbent.

## Status: 🟡 Built, waiting on data — `app/training/meta.py`,
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
