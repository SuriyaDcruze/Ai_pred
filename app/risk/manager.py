"""Risk management: ATR-based stops, R-multiple targets, and position sizing.

Sizing is derived from the fixed-fractional rule: never risk more than
``max_account_risk`` of equity on a single trade. Position size follows directly
from the per-unit stop distance, so the account-risk cap is a hard guarantee,
not a suggestion.
"""

from __future__ import annotations

from app.config import settings
from app.data.schemas import RiskPlan, Side


class RiskManager:
    def __init__(
        self,
        account_equity: float | None = None,
        max_account_risk: float | None = None,
        min_rr: float | None = None,
    ):
        self.equity = account_equity if account_equity is not None else settings.account_equity
        self.max_risk = max_account_risk if max_account_risk is not None else settings.max_account_risk
        self.min_rr = min_rr if min_rr is not None else settings.min_rr

    def build_plan(
        self,
        side: Side,
        entry: float,
        atr: float,
        *,
        sl_atr_mult: float = 1.5,
        tp1_r: float = 2.0,
        tp2_r: float = 3.0,
        entry_band_atr: float = 0.25,
    ) -> RiskPlan | None:
        """Construct a full risk plan for a directional trade.

        Returns ``None`` for a non-directional side or degenerate ATR. Stop is
        ``sl_atr_mult`` ATR from entry; targets are R-multiples of the stop
        distance so R:R is exact by construction.
        """
        if side not in (Side.BUY, Side.SELL) or atr <= 0 or entry <= 0:
            return None

        stop_dist = sl_atr_mult * atr
        if stop_dist <= 0:
            return None
        band = entry_band_atr * atr

        if side is Side.BUY:
            entry_low, entry_high = entry - band, entry + band
            stop = entry - stop_dist
            tp1 = entry + tp1_r * stop_dist
            tp2 = entry + tp2_r * stop_dist
        else:  # SELL
            entry_low, entry_high = entry - band, entry + band
            stop = entry + stop_dist
            tp1 = entry - tp1_r * stop_dist
            tp2 = entry - tp2_r * stop_dist

        risk_per_unit = abs(entry - stop)
        reward_per_unit = abs(tp1 - entry)
        rr = reward_per_unit / risk_per_unit if risk_per_unit > 0 else 0.0

        risk_capital = self.equity * self.max_risk
        position_size = risk_capital / risk_per_unit if risk_per_unit > 0 else 0.0

        return RiskPlan(
            entry_low=round(entry_low, 8),
            entry_high=round(entry_high, 8),
            stop_loss=round(stop, 8),
            take_profit_1=round(tp1, 8),
            take_profit_2=round(tp2, 8),
            risk_reward=round(rr, 2),
            position_size=round(position_size, 8),
            account_risk_pct=round(self.max_risk * 100, 4),
        )

    def meets_min_rr(self, plan: RiskPlan | None) -> bool:
        return plan is not None and plan.risk_reward >= self.min_rr
