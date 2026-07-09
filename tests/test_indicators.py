"""Unit tests for technical indicators — correctness against known properties."""

import numpy as np
import pandas as pd

from app.indicators import technical as ti
from app.indicators.technical import add_all_indicators


def test_rsi_bounds(ohlcv):
    r = ti.rsi(ohlcv["close"])
    valid = r.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_rsi_all_gains_is_100():
    close = pd.Series(np.arange(1, 50, dtype=float))
    r = ti.rsi(close, period=14)
    assert r.iloc[-1] == 100.0


def test_ema_matches_manual():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    e = ti.ema(s, 2)
    # EMA alpha = 2/(2+1)=0.667; verify recursion at last point
    assert abs(e.iloc[-1] - s.ewm(span=2, adjust=False).mean().iloc[-1]) < 1e-9


def test_atr_positive(ohlcv):
    a = ti.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    assert (a.dropna() >= 0).all()


def test_macd_hist_is_diff(ohlcv):
    m = ti.macd(ohlcv["close"])
    assert np.allclose((m["macd"] - m["macd_signal"]).dropna(), m["macd_hist"].dropna())


def test_supertrend_direction_is_pm1(ohlcv):
    st = ti.supertrend(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    assert set(np.unique(st["supertrend_dir"].dropna())).issubset({-1.0, 1.0})


def test_bollinger_ordering(ohlcv):
    bb = ti.bollinger_bands(ohlcv["close"])
    valid = bb.dropna()
    assert (valid["bb_upper"] >= valid["bb_mid"]).all()
    assert (valid["bb_mid"] >= valid["bb_lower"]).all()


def test_adx_bounds(ohlcv):
    a = ti.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    valid = a["adx"].dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_pivots_no_lookahead(ohlcv):
    p = ti.pivot_points(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    # First row must be NaN because it depends on the previous bar.
    assert np.isnan(p["pivot"].iloc[0])


def test_add_all_indicators_no_crash_and_columns(ohlcv):
    out = add_all_indicators(ohlcv)
    for col in ["ema_9", "rsi", "macd", "atr", "adx", "vwap", "obv", "supertrend"]:
        assert col in out.columns
    assert len(out) == len(ohlcv)


def test_add_all_indicators_missing_columns_raises():
    import pytest

    with pytest.raises(ValueError):
        add_all_indicators(pd.DataFrame({"close": [1, 2, 3]}))
