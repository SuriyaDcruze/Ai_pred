# Volume 10 — News Intelligence

## Purpose
Bring the one input that is **not derived from price** — news — into Aegis as honest
**context**: is sentiment bullish/bearish, and are high-impact events (earnings, RBI,
budget) near?

## Status: 🟡 Basic — `app/features/sentiment.py` (lexicon sentiment over RSS)

## Responsibilities (target)
- Fetch headlines for a symbol/market from Indian sources.
- Classify: **Bullish / Bearish / Neutral**, **High / Low impact**, **Company / Sector /
  Market-wide**.
- Surface as context (and flag "avoid trading into a known event").

## Current implementation
- `fetch_news()` — Yahoo Finance RSS, no API key; finance-tuned lexicon with negation
  handling; returns `NewsSentiment { score, label, headlines }`. Symbol mapping
  (BTCUSDT→BTC-USD, stocks pass through).
- Surfaced on the dashboard News card and available to the intelligence layer.

## Inputs / Outputs
- **In:** symbol → news ticker; RSS feed.
- **Out:** aggregate sentiment score (−1..+1), label, scored headlines.

## Architecture (target)
```
Sources (Moneycontrol, ET Markets, Business Standard, LiveMint, NSE/BSE filings)
  → fetch + dedupe → classify (sentiment + impact + scope)
  → event calendar (earnings, dividend, RBI, budget)
  → context object → Intelligence layer + "event risk" flag
```

## Data
- (Future) a small store of headlines + classifications keyed by symbol/date, feeding
  Historical Memory ("what news accompanied this setup?").

## Failure / logging
- News is **best-effort**: any fetch/parse error returns a neutral empty result so the
  dashboard and decision layer keep working. Never fatal.

## Testing
- `tests/test_sentiment.py` — lexicon scoring, negation, symbol mapping.

## Prediction-Model integration
- **Context only.** News sentiment is NOT a model feature (adding it would require a
  retrain and is unproven — RESULTS notes it as a *candidate*, not an edge). It informs
  the *user* and can gate trading around events; it does not compute the decision.

## LLM integration
- The assistant summarises the news mood and flags event risk when explaining a call.

## Compliance note
- Headlines are untrusted text — never treated as instructions (prompt-injection), and we
  never present news as a recommendation to act.

## Future
- Indian sources + event calendar; impact classification; "block/soften recommendations
  near high-impact events"; corporate-action awareness (splits, bonus, results dates).
