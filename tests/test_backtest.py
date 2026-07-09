"""Tests for the backtesting engine — no lookahead, sane metrics, cost impact."""

import numpy as np
import pandas as pd

from app.backtest.engine import Backtester
from app.data.schemas import Side
from app.data.synthetic import generate_ohlcv


def test_backtest_runs_and_reports(ohlcv):
    bt = Backtester(warmup=210, max_hold_bars=24)
    df = generate_ohlcv(n=1200, seed=3)
    result = bt.run(df, timeframe="1h")
    s = result.summary()
    assert s["bars_tested"] > 0
    assert s["trades"] == result.n_trades
    # Equity curve starts at the configured equity.
    assert result.equity_curve[0] == result.start_equity
    # Win rate is a valid probability.
    assert 0.0 <= result.win_rate <= 1.0


def test_no_lookahead_exit_after_entry():
    df = generate_ohlcv(n=1000, seed=5)
    bt = Backtester(warmup=210, max_hold_bars=24)
    result = bt.run(df)
    for t in result.trades:
        # Every trade must exit strictly after it entered.
        assert t.exit_index > t.entry_index
        # And within the max hold horizon.
        assert t.exit_index - t.entry_index <= 24


def test_costs_reduce_pnl():
    df = generate_ohlcv(n=1000, seed=9)
    no_cost = Backtester(fee_pct=0.0, slippage_pct=0.0, warmup=210).run(df)
    with_cost = Backtester(fee_pct=0.001, slippage_pct=0.001, warmup=210).run(df)
    # With identical signals, adding costs cannot improve total PnL.
    if no_cost.n_trades and no_cost.n_trades == with_cost.n_trades:
        assert with_cost.final_equity <= no_cost.final_equity + 1e-6


def test_max_drawdown_non_negative(ohlcv):
    result = Backtester(warmup=210).run(generate_ohlcv(n=1000, seed=2))
    assert result.max_drawdown_pct >= 0.0
