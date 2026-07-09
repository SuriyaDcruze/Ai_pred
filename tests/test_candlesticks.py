"""Tests for candlestick pattern detectors codified from the trading book."""

import pandas as pd

from app.features.candlesticks import (
    CANDLE_FEATURE_COLUMNS,
    add_candlestick_patterns,
)
from app.features.engineering import FeatureBuilder


def _df(rows):
    idx = pd.date_range("2023-01-01", periods=len(rows), freq="h")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(
        volume=1.0, trades=1
    )


def test_bullish_engulfing():
    # bar0 bearish (10->9), bar1 bullish engulfing (8.8->10.2 covers 9..10)
    df = _df([[10.0, 10.1, 8.9, 9.0], [8.8, 10.3, 8.7, 10.2]])
    out = add_candlestick_patterns(df)
    assert out["cdl_engulf_bull"].iloc[1] == 1.0
    assert out["cdl_engulf_bear"].iloc[1] == 0.0


def test_bearish_engulfing():
    df = _df([[9.0, 10.1, 8.9, 10.0], [10.2, 10.3, 8.7, 8.8]])
    out = add_candlestick_patterns(df)
    assert out["cdl_engulf_bear"].iloc[1] == 1.0


def test_bullish_pin_bar():
    # small body near top, long lower wick
    df = _df([[10.0, 10.2, 8.0, 10.05]])
    out = add_candlestick_patterns(df)
    assert out["cdl_pin_bull"].iloc[0] == 1.0
    assert out["cdl_hammer"].iloc[0] == 1.0


def test_shooting_star_pin():
    df = _df([[10.0, 12.0, 9.95, 10.05]])
    out = add_candlestick_patterns(df)
    assert out["cdl_pin_bear"].iloc[0] == 1.0


def test_inside_bar():
    df = _df([[10.0, 12.0, 8.0, 11.0], [10.5, 11.0, 9.0, 10.0]])
    out = add_candlestick_patterns(df)
    assert out["cdl_inside_bar"].iloc[1] == 1.0


def test_doji():
    df = _df([[10.0, 10.5, 9.5, 10.0]])
    out = add_candlestick_patterns(df)
    assert out["cdl_doji"].iloc[0] == 1.0


def test_morning_star():
    # big bear, small body gap down, big bull closing into first body
    df = _df([
        [12.0, 12.1, 10.0, 10.1],   # big bearish
        [9.9, 10.0, 9.7, 9.85],     # small body
        [9.9, 11.6, 9.8, 11.5],     # big bullish closing above midpoint(11.05)
    ])
    out = add_candlestick_patterns(df)
    assert out["cdl_morning_star"].iloc[2] == 1.0


def test_scores_and_signal():
    df = _df([[10.0, 10.1, 8.9, 9.0], [8.8, 10.3, 8.7, 10.2]])
    out = add_candlestick_patterns(df)
    assert out["cdl_bull_score"].iloc[1] >= 1.0
    assert out["cdl_signal"].iloc[1] >= 1.0


def test_feature_builder_includes_candles(ohlcv):
    fb = FeatureBuilder()
    frame = fb.build_frame(ohlcv)
    for col in CANDLE_FEATURE_COLUMNS:
        assert col in frame.columns
    assert "cdl_confluence" in frame.columns
    # Feature matrix builds and scales without NaNs/inf.
    arr = fb.fit_transform(ohlcv)
    assert arr.shape[1] == fb.n_features
    assert not (arr != arr).any()  # no NaNs
