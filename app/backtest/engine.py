"""Event-driven backtester — the honesty check for the whole platform.

It walks the test candles one bar at a time, and at each *closed* bar asks the
same AnalysisService the live app uses for a signal. When a BUY/SELL fires, it
opens a simulated trade and then resolves it against the **future** high/low
bar-by-bar to see whether the stop or a target is hit first — with taker fees
and slippage applied on entry and exit.

Critical correctness properties:
  * No lookahead — the decision at bar ``i`` uses only candles ``[0..i]``; the
    outcome is resolved using bars strictly after ``i``.
  * Conservative stop-first rule — if a single bar's range spans both the stop
    and the target, we assume the **stop** hit first (we can't see intrabar
    order, so we take the pessimistic outcome).
  * One position at a time — no pyramiding, no overlapping trades.

The metrics it reports (expectancy, profit factor, max drawdown) are what tell
you whether the model has a real edge or is just drawing confident-looking lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.data.schemas import Side
from app.service import AnalysisService
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Trade:
    entry_index: int
    exit_index: int
    side: str
    entry: float
    stop: float
    target: float
    exit_price: float
    outcome: str  # "win" | "loss" | "timeout"
    r_multiple: float
    pnl: float
    equity_after: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    start_equity: float = 0.0
    final_equity: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    bars_tested: int = 0

    # --- summary metrics ---
    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.outcome == "win")

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.outcome == "loss")

    @property
    def win_rate(self) -> float:
        decided = self.wins + self.losses
        return self.wins / decided if decided else 0.0

    @property
    def total_return_pct(self) -> float:
        if self.start_equity == 0:
            return 0.0
        return 100.0 * (self.final_equity / self.start_equity - 1.0)

    @property
    def gross_profit(self) -> float:
        return sum(t.pnl for t in self.trades if t.pnl > 0)

    @property
    def gross_loss(self) -> float:
        return -sum(t.pnl for t in self.trades if t.pnl < 0)

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss > 0 else float("inf")

    @property
    def expectancy_r(self) -> float:
        return float(np.mean([t.r_multiple for t in self.trades])) if self.trades else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        arr = np.asarray(self.equity_curve, dtype=float)
        peak = np.maximum.accumulate(arr)
        dd = (arr - peak) / peak
        return float(-dd.min() * 100.0)

    @property
    def sharpe(self) -> float:
        """Per-trade Sharpe (mean R / std R). Not annualized — a relative gauge."""
        if len(self.trades) < 2:
            return 0.0
        rs = np.array([t.r_multiple for t in self.trades])
        return float(rs.mean() / rs.std()) if rs.std() > 0 else 0.0

    def summary(self) -> dict:
        return {
            "bars_tested": self.bars_tested,
            "trades": self.n_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate_pct": round(self.win_rate * 100, 1),
            "total_return_pct": round(self.total_return_pct, 2),
            "profit_factor": round(self.profit_factor, 2) if np.isfinite(self.profit_factor) else None,
            "expectancy_r": round(self.expectancy_r, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_per_trade": round(self.sharpe, 2),
            "final_equity": round(self.final_equity, 2),
        }


class Backtester:
    def __init__(
        self,
        service: AnalysisService | None = None,
        fee_pct: float = 0.0004,       # 0.04% taker per side (Binance spot ~0.1%; futures ~0.04%)
        slippage_pct: float = 0.0002,  # 0.02% adverse slippage per side
        max_hold_bars: int = 48,       # force-close a trade after this many bars
        warmup: int = 210,             # bars needed for indicators (EMA200 etc.)
        window: int = 400,             # rolling context passed to the analyzer
    ):
        self.service = service or AnalysisService()
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.max_hold_bars = max_hold_bars
        self.warmup = warmup
        self.window = window

    def run(
        self,
        ohlcv: pd.DataFrame,
        symbol: str = "BTCUSDT",
        exchange: str = "binance",
        timeframe: str = "1h",
        start_equity: float = 10_000.0,
        risk_per_trade: float = 0.01,
    ) -> BacktestResult:
        n = len(ohlcv)
        highs = ohlcv["high"].to_numpy()
        lows = ohlcv["low"].to_numpy()
        closes = ohlcv["close"].to_numpy()

        equity = start_equity
        result = BacktestResult(start_equity=start_equity, equity_curve=[equity])
        i = self.warmup
        tested = 0
        total = n - 1 - self.warmup
        logger.info("Backtesting %d bars…", max(0, total))

        while i < n - 1:
            tested += 1
            # Heartbeat every 200 bars so long runs never look frozen.
            if tested % 200 == 0:
                pct = 100.0 * (i - self.warmup) / max(1, total)
                logger.info("  ...%.0f%% (%d/%d bars, %d trades, equity %.0f)",
                            pct, i - self.warmup, total, len(result.trades), equity)
            ctx = ohlcv.iloc[max(0, i - self.window) : i + 1]
            try:
                signal = self.service.analyze(ctx, symbol, exchange, timeframe)
            except ValueError:
                i += 1
                continue

            if signal.decision not in (Side.BUY, Side.SELL) or signal.risk is None:
                i += 1
                continue

            side = signal.decision
            entry = closes[i]  # enter at the close that produced the signal
            plan = signal.risk
            stop, target = plan.stop_loss, plan.take_profit_1

            trade = self._resolve_trade(
                side, entry, stop, target, i, n, highs, lows, closes,
                equity, risk_per_trade,
            )
            equity = trade.equity_after
            result.trades.append(trade)
            result.equity_curve.append(equity)
            # Jump past the trade — no overlapping positions.
            i = trade.exit_index + 1

        result.final_equity = equity
        result.bars_tested = tested
        logger.info("Backtest done: %s", result.summary())
        return result

    def _resolve_trade(
        self, side, entry, stop, target, i, n, highs, lows, closes, equity, risk_per_trade,
    ) -> Trade:
        long = side is Side.BUY
        risk_amount = equity * risk_per_trade
        risk_per_unit = abs(entry - stop)
        size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0

        exit_index = min(i + self.max_hold_bars, n - 1)
        exit_price = closes[exit_index]
        outcome = "timeout"

        for j in range(i + 1, min(i + self.max_hold_bars, n - 1) + 1):
            hit_stop = lows[j] <= stop if long else highs[j] >= stop
            hit_tp = highs[j] >= target if long else lows[j] <= target
            if hit_stop and hit_tp:
                # Pessimistic: assume the stop filled first (can't see intrabar).
                exit_index, exit_price, outcome = j, stop, "loss"
                break
            if hit_stop:
                exit_index, exit_price, outcome = j, stop, "loss"
                break
            if hit_tp:
                exit_index, exit_price, outcome = j, target, "win"
                break

        # Costs: fee + slippage on both entry and exit, applied against us.
        cost_rate = self.fee_pct + self.slippage_pct
        gross = (exit_price - entry) * size if long else (entry - exit_price) * size
        costs = (entry + exit_price) * size * cost_rate
        pnl = gross - costs
        equity_after = equity + pnl
        r_multiple = pnl / risk_amount if risk_amount > 0 else 0.0

        return Trade(
            entry_index=i, exit_index=exit_index, side=side.value,
            entry=round(entry, 6), stop=round(stop, 6), target=round(target, 6),
            exit_price=round(exit_price, 6), outcome=outcome,
            r_multiple=round(r_multiple, 3), pnl=round(pnl, 2),
            equity_after=round(equity_after, 2),
        )
