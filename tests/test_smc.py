"""Tests for SMC structural features."""

import numpy as np
import pandas as pd

from app.features.smc import (
    add_smc_features,
    fair_value_gaps,
    market_structure,
    order_blocks,
    swing_points,
)


def _frame(highs, lows, opens=None, closes=None):
    n = len(highs)
    opens = opens or list(np.array(lows) + 0.1)
    closes = closes or list(np.array(highs) - 0.1)
    idx = pd.date_range("2023-01-01", periods=n, freq="h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1.0] * n},
        index=idx,
    )


def test_swing_high_detected():
    highs = [1, 2, 3, 5, 3, 2, 1]  # peak at index 3
    lows = [0, 1, 2, 4, 2, 1, 0]
    sp = swing_points(pd.Series(highs, dtype=float), pd.Series(lows, dtype=float), width=2)
    assert bool(sp["swing_high"].iloc[3])


def test_bullish_fvg_detected():
    # Gap up: low[2]=10 > high[0]=5
    highs = [5.0, 8.0, 12.0]
    lows = [4.0, 6.0, 10.0]
    fvg = fair_value_gaps(pd.Series(highs), pd.Series(lows))
    assert fvg["fvg_bull"].iloc[2] == 1.0
    assert fvg["fvg_size"].iloc[2] > 0


def test_no_fvg_when_no_gap():
    highs = [5.0, 6.0, 7.0]
    lows = [4.0, 4.5, 5.0]  # low[2]=5 < high[0]=5 -> no gap
    fvg = fair_value_gaps(pd.Series(highs), pd.Series(lows))
    assert fvg["fvg_bull"].iloc[2] == 0.0


def test_market_structure_trend_values():
    df = _frame(
        highs=[10, 11, 12, 13, 12, 11, 10, 9],
        lows=[9, 10, 11, 12, 11, 10, 9, 8],
    )
    ms = market_structure(df["high"], df["low"], df["close"])
    assert set(np.unique(ms["structure_trend"])).issubset({-1.0, 0.0, 1.0})
    assert set(np.unique(ms["bos"])).issubset({-1.0, 0.0, 1.0})


def test_order_block_bull():
    # down candle then strong up candle closing above its high
    df = _frame(
        highs=[10.0, 12.0],
        lows=[8.0, 9.0],
        opens=[9.5, 9.0],
        closes=[8.5, 11.5],  # candle0 down, candle1 up closing > high0(10)
    )
    ob = order_blocks(df["open"], df["high"], df["low"], df["close"])
    assert ob["ob_bull"].iloc[0] == 1.0


def test_add_smc_features_columns(ohlcv):
    out = add_smc_features(ohlcv)
    for col in ["bos", "choch", "structure_trend", "fvg_bull", "ob_bull", "sell_liquidity_dist"]:
        assert col in out.columns
