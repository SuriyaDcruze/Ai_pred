# Volume 14 — Similarity Engine

## Purpose
Answer *"I have seen this setup before"* — find the most similar historical situations and
report how trades actually fared in them. Pure **explainability**, honestly labelled.

## Status: 🟢 Built — `app/ai/similarity_engine.py`

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
