# Volume 08 — Market Intelligence

## Purpose
Understand the **market context** around a stock and assemble the full **explainable
recommendation** — the layer that makes Aegis an analyst, not a black box.

## Status: 🟢 Built — `app/intelligence.py`, `app/features/` (indicators, SMC, patterns)

## Responsibilities
- Classify **market state** (trend strength + volatility) with honest, rule-based labels
  (we proved learned regimes are noise — so this is transparent rules, not a black box).
- Compute **relative strength vs Nifty** (context).
- Assemble market state + sector (Vol 09) + similarity (Vol 14) + the model decision +
  a risk-defined plan + **plain-English For/Against factors** into one report.
- Produce the final **BUY / SELL / WAIT** (WAIT unless direction≠WAIT AND outcome=TAKE).

## Inputs / Outputs
- **In:** symbol, timeframe; internally: features, Prediction & Outcome outputs, sector,
  similarity, Nifty series.
- **Out:** `{ recommendation, leaning, direction_confidence, outcome_probability, decision,
  market_state, sector, relative_strength_vs_nifty_pct, historical_similarity, plan,
  positive_factors, negative_factors, explanation, disclaimer }`.

## Architecture
- `_market_state()` — ADX + EMA alignment → trend label; ATR% → volatility label.
- `_reasons()` — derives For/Against factors from real feature values (trend agreement,
  momentum confirmation, RSI stretch, relative strength, sector, similarity).
- `analyze_stock()` — orchestrates all engines; NSE symbols use the NSE outcome model.

## API integration
- `GET /intelligence?symbol=&timeframe=` → dashboard **Deep Analysis** card.

## Data
- No new store; reads market data + model outputs live. Context features (India, sector)
  are **explainability inputs, not model features** (they add no edge — proven).

## Failure / logging
- Any sub-engine (sector, similarity) failing → that section drops to null; the core
  recommendation still renders.

## Testing
- Covered via endpoint smoke + the underlying model/sector/similarity unit tests.
- Target: a unit test asserting the recommendation logic (WAIT unless direction≠WAIT AND
  outcome=TAKE).

## Prediction-Model integration
- The **decision comes from the models**; market state and relative strength are context
  that shapes the *explanation and For/Against*, never the decision.

## LLM integration
- The `/intelligence` response is the primary structured input the Conversation layer
  explains ("why BUY / why WAIT").

## Future
- Market breadth, India VIX, advance/decline, gap behaviour as **context** (not features);
  "what changed since yesterday" via Historical Memory.
