# Volume 11 — Portfolio Intelligence

## Purpose
Answer *"I have ₹X — how should I deploy it?"* with a **risk-defined, diversified,
correlation-aware allocation** across today's high-quality setups.

## Status: 🔴 Not built — specified here.

## Responsibilities
- Take a capital amount + risk appetite (from User Profile, Vol 16).
- Select from today's **TAKE** setups (Screener/Outcome Engine).
- Size each position so per-trade risk ≤ configured max (default 1% of capital).
- Enforce **diversification** (cap exposure per sector) and **correlation** limits.
- Report expected R, expected drawdown, and total risk deployed.

## Inputs / Outputs
- **In:** `capital`, `max_risk_per_trade`, `max_sector_weight`, current TAKE setups
  (symbol, entry, stop, sector, outcome probability).
- **Out:** `{ allocations: [{ symbol, shares/qty, capital_allocated, risk_amount,
  sector }], cash_reserved, total_risk_pct, sector_weights, expected_R, notes }`.

## Architecture (target)
```
Screener TAKE setups
  → filter by outcome probability (highest conviction first)
  → position size = (capital × risk%) / (entry − stop)   [risk-based sizing]
  → apply sector cap + correlation cap (drop/scale overlapping names)
  → normalise to available capital, reserve cash
  → allocation + expected-risk report
```
Reuses the **Risk Engine** (Vol 12) for per-trade sizing; adds portfolio-level
constraints. Pure, deterministic, testable — **no model changes.**

## API integration (target)
- `POST /portfolio { capital, risk_pct, max_sector_weight }` → allocation.
- Conversational: "I have ₹3 lakh, build me a portfolio" → this engine.

## Data
- Correlation matrix from recent returns of the candidate names (past-only, cached).

## Failure / logging
- Fewer TAKE setups than desired positions → allocate what qualifies, reserve the rest
  as cash (never force weak trades to "fill" the portfolio).

## Testing (target)
- Sizing math (risk ≤ cap), sector-weight cap respected, correlation cap drops overlaps,
  capital conservation (sum ≤ capital), zero-TAKE → all cash.

## Prediction-Model integration
- Consumes Outcome-filtered setups; never predicts. Allocation quality inherits the
  (backtest-only) edge — so outputs carry the same "not proven live" disclaimer.

## LLM integration
- The assistant explains the allocation, the diversification logic, and the risk taken.

## Honest caveat
- A portfolio built on a **backtest-only** edge is still unproven. This engine sizes
  *risk* correctly regardless — but expected-return figures are illustrative until the
  live track record (Vol 18) exists.

## Future
- Rebalancing, tax-aware (STCG/LTCG) suggestions, goal-based allocation, index-hedged
  variants.
