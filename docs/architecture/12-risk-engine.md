# Volume 12 — Risk Engine

## Purpose
Turn a direction into a **risk-defined plan** — stop, targets, and position size — and
enforce hard capital-protection rules. Capital protection > trade frequency.

## Status: 🟢 Built — `app/risk/manager.py`, `app/decision/rules.py`

## Responsibilities
- Compute an **ATR-based stop** (default 1.5×ATR) and **R-multiple targets** (2R, 3R).
- Size the position so the loss at stop ≤ **max account risk** (default 1%).
- Enforce a minimum **risk:reward** (default 2:1) — else downgrade to WAIT.
- Provide the "My Rules" personal-discipline checklist (a second, user-owned gate).

## Inputs / Outputs
- **In:** side (BUY/SELL), entry, ATR, account equity, config (risk %, R:R, ATR mult).
- **Out:** `RiskPlan { entry_low/high, stop_loss, take_profit_1/2, risk_reward,
  position_size, account_risk_pct }`.

## Architecture
- `RiskManager.build_plan()` — stop distance = mult×ATR; targets = R-multiples; size
  derived from the per-unit stop distance so the account-risk cap is a **hard guarantee**.
- Hard rules (documented invariants): never all-in; never martingale; never widen a stop
  to "give it room"; the stop is set from *invalidation*, not desired profit.
- **My Rules** (`decision/rules.py`) — a user-configurable checklist (min confidence,
  never fight the trend, min R:R, don't chase, daily trade cap, calm-market) persisted in
  SQLite; a discipline layer, explicitly **not** an accuracy layer.

## API integration
- Feeds `/analyze`, `/intelligence`, `/screener/nse` (the plan); `/rules` (checklist).

## Failure / logging
- ATR ≤ 0 or entry ≤ 0 → no plan (returns None), never a nonsensical stop.

## Testing
- `tests/test_rules.py` — every gate (confidence, trend, R:R, overbought, news, daily cap,
  volatility), plus persistence and the WAIT-state fix.

## Prediction-Model integration
- Consumes the model's side + ATR; the **plan levels come from Risk, never from the LLM.**

## LLM integration
- The assistant explains the stop/target/size and the R:R — reading them from the plan.

## Future
- Portfolio-level risk (Vol 11) — total open risk, correlation-aware caps; volatility
  targeting; trailing-stop variants (validated before promotion).
