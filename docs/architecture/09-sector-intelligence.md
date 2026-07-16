# Volume 09 — Sector Intelligence

## Purpose
Understand the **sector before the stock.** Indian equities move by sector rotation — a
bank-stock long is lower quality when Banking is weak, whatever the chart says.

## Status: 🟢 Built — `app/sector.py`

## Responsibilities
- Rank the 10 major NSE sectors (Banking, IT, Auto, Pharma, FMCG, Energy, Metal, Realty,
  Infra, PSU Bank) by **relative strength vs Nifty 50 + momentum**.
- Map each liquid NSE stock → its sector.
- Answer: does the sector **support / oppose / stay neutral** to a long/short?

## Inputs / Outputs
- **In:** NSE sector indices (`^NSEBANK`, `^CNXIT`, …) + Nifty (`^NSEI`) via Yahoo.
- **Out:** ranked sectors `{ sector, label(Strong/Weak/Neutral), rank, rs20, score,
  above_ema50 }`; per-stock sector context.

## Architecture
- `_strength()` — sector 20/50-day return minus Nifty's (relative strength) + EMA-50
  position + slope. Past-only, leakage-safe.
- Score = rs20 + momentum bonus; sectors ranked; labelled Strong/Weak/Neutral.
- **15-min cache** so the screener/intelligence don't refetch indices per call.

## API integration
- `GET /sectors` (full ranking); folded into `/intelligence` For/Against factors + a
  sector tile on the Deep Analysis card.

## Failure / logging
- Sector index fetch fails → that sector omitted; stock context degrades to "Unknown"
  (neutral), never blocks the recommendation.

## Testing
- `tests/test_sector.py` (8) — strength scoring, stock→sector map, support/against logic.

## Prediction-Model integration
- **Context only.** Sector strength shapes the explanation and For/Against — it is **not**
  a model feature (features add no edge — proven). Honest by design.

## LLM integration
- The assistant cites sector strength ("IT is strong, rank 1/10") when explaining a call.

## Future
- Sector momentum trends, rotation detection (money moving IT→Banking), sector-level
  historical memory; per-sector outcome models if data supports.
