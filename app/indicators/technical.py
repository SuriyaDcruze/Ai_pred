"""Vectorized technical indicators.

Every function takes and returns pandas Series/DataFrames and is dependency-free
(pure numpy/pandas) so it runs identically on CPU boxes and in CI. All functions
are NaN-safe at the head of the series (warm-up period) and never look ahead.

References: Wilder (RSI/ATR/ADX), Appel (MACD), Bollinger, Lane (Stochastic),
Olson (SuperTrend), Donchian, Lambert (CCI), and standard VWAP/OBV/MFI formulas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Moving averages
# --------------------------------------------------------------------------- #


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wilder_ema(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (used by RSI/ATR/ADX). Alpha = 1/period."""
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


# --------------------------------------------------------------------------- #
# Momentum
# --------------------------------------------------------------------------- #


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = wilder_ema(gain, period)
    avg_loss = wilder_ema(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(100.0).where(avg_loss != 0, 100.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "macd_signal": signal_line, "macd_hist": hist}
    )


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3
) -> pd.DataFrame:
    lowest = low.rolling(k, min_periods=k).min()
    highest = high.rolling(k, min_periods=k).max()
    percent_k = 100.0 * (close - lowest) / (highest - lowest).replace(0.0, np.nan)
    percent_d = percent_k.rolling(d, min_periods=d).mean()
    return pd.DataFrame({"stoch_k": percent_k, "stoch_d": percent_d})


def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    ma = tp.rolling(period, min_periods=period).mean()
    mad = tp.rolling(period, min_periods=period).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    return (tp - ma) / (0.015 * mad.replace(0.0, np.nan))


def roc(close: pd.Series, period: int = 12) -> pd.Series:
    return 100.0 * (close - close.shift(period)) / close.shift(period)


def mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    tp = (high + low + close) / 3.0
    raw_flow = tp * volume
    direction = np.sign(tp.diff().fillna(0.0))
    pos_flow = raw_flow.where(direction > 0, 0.0).rolling(period, min_periods=period).sum()
    neg_flow = raw_flow.where(direction < 0, 0.0).rolling(period, min_periods=period).sum()
    ratio = pos_flow / neg_flow.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + ratio))


# --------------------------------------------------------------------------- #
# Volatility
# --------------------------------------------------------------------------- #


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return wilder_ema(true_range(high, low, close), period)


def bollinger_bands(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> pd.DataFrame:
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0.0, np.nan)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width}
    )


def donchian(high: pd.Series, low: pd.Series, period: int = 20) -> pd.DataFrame:
    upper = high.rolling(period, min_periods=period).max()
    lower = low.rolling(period, min_periods=period).min()
    return pd.DataFrame(
        {"donchian_upper": upper, "donchian_lower": lower, "donchian_mid": (upper + lower) / 2.0}
    )


# --------------------------------------------------------------------------- #
# Trend
# --------------------------------------------------------------------------- #


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.DataFrame:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = true_range(high, low, close)
    atr_ = wilder_ema(tr, period)
    plus_di = 100.0 * wilder_ema(plus_dm, period) / atr_.replace(0.0, np.nan)
    minus_di = 100.0 * wilder_ema(minus_dm, period) / atr_.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_ = wilder_ema(dx, period)
    return pd.DataFrame({"adx": adx_, "plus_di": plus_di, "minus_di": minus_di})


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """SuperTrend. Returns the line and a +1/-1 direction flag.

    Implemented iteratively because each band depends on the previous bar's
    band and trend state — a genuine recursive indicator, not a rolling window.
    """
    atr_ = atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + multiplier * atr_
    lower_basic = hl2 - multiplier * atr_

    n = len(close)
    st = np.full(n, np.nan)
    direction = np.ones(n, dtype=float)  # +1 uptrend, -1 downtrend
    final_upper = upper_basic.to_numpy(copy=True)
    final_lower = lower_basic.to_numpy(copy=True)
    c = close.to_numpy()

    for i in range(1, n):
        if np.isnan(atr_.iloc[i]):
            continue
        final_upper[i] = (
            upper_basic.iloc[i]
            if (upper_basic.iloc[i] < final_upper[i - 1] or c[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            lower_basic.iloc[i]
            if (lower_basic.iloc[i] > final_lower[i - 1] or c[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )
        prev = st[i - 1]
        if np.isnan(prev):
            direction[i] = 1.0
            st[i] = final_lower[i]
        elif prev == final_upper[i - 1]:
            direction[i] = -1.0 if c[i] <= final_upper[i] else 1.0
        else:
            direction[i] = 1.0 if c[i] >= final_lower[i] else -1.0
        st[i] = final_lower[i] if direction[i] > 0 else final_upper[i]

    return pd.DataFrame(
        {"supertrend": st, "supertrend_dir": direction}, index=close.index
    )


def ichimoku(
    high: pd.Series, low: pd.Series, close: pd.Series
) -> pd.DataFrame:
    conv = (high.rolling(9).max() + low.rolling(9).min()) / 2.0
    base = (high.rolling(26).max() + low.rolling(26).min()) / 2.0
    span_a = ((conv + base) / 2.0).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2.0).shift(26)
    return pd.DataFrame(
        {
            "ichimoku_conv": conv,
            "ichimoku_base": base,
            "ichimoku_span_a": span_a,
            "ichimoku_span_b": span_b,
        }
    )


# --------------------------------------------------------------------------- #
# Volume
# --------------------------------------------------------------------------- #


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Rolling cumulative VWAP anchored at the start of the frame."""
    tp = (high + low + close) / 3.0
    cum_vol = volume.cumsum().replace(0.0, np.nan)
    return (tp * volume).cumsum() / cum_vol


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def volume_delta(open_: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Signed volume: buys (close>open) positive, sells negative.

    A candle-level proxy for order-flow delta when tick data is unavailable.
    """
    sign = np.where(close >= open_, 1.0, -1.0)
    return pd.Series(sign * volume.to_numpy(), index=close.index)


# --------------------------------------------------------------------------- #
# Pivots
# --------------------------------------------------------------------------- #


def pivot_points(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.DataFrame:
    """Classic floor-trader pivots computed from the *previous* bar (no lookahead)."""
    p = (high.shift(1) + low.shift(1) + close.shift(1)) / 3.0
    r1 = 2 * p - low.shift(1)
    s1 = 2 * p - high.shift(1)
    r2 = p + (high.shift(1) - low.shift(1))
    s2 = p - (high.shift(1) - low.shift(1))
    return pd.DataFrame({"pivot": p, "pivot_r1": r1, "pivot_s1": s1, "pivot_r2": r2, "pivot_s2": s2})


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` (OHLCV) augmented with every indicator column.

    ``df`` must contain columns: open, high, low, close, volume.
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"add_all_indicators: missing columns {missing}")

    o, h, l, c, v = (df["open"], df["high"], df["low"], df["close"], df["volume"])
    out = df.copy()

    out["ema_9"] = ema(c, 9)
    out["ema_21"] = ema(c, 21)
    out["ema_50"] = ema(c, 50)
    out["ema_200"] = ema(c, 200)
    out["sma_20"] = sma(c, 20)
    out["sma_50"] = sma(c, 50)

    out["rsi"] = rsi(c)
    out = out.join(macd(c))
    out = out.join(stochastic(h, l, c))
    out["cci"] = cci(h, l, c)
    out["roc"] = roc(c)
    out["mfi"] = mfi(h, l, c, v)

    out["atr"] = atr(h, l, c)
    out = out.join(bollinger_bands(c))
    out = out.join(donchian(h, l))

    out = out.join(adx(h, l, c))
    out = out.join(supertrend(h, l, c))
    out = out.join(ichimoku(h, l, c))

    out["vwap"] = vwap(h, l, c, v)
    out["obv"] = obv(c, v)
    out["volume_delta"] = volume_delta(o, c, v)
    out = out.join(pivot_points(h, l, c))

    # Candle anatomy
    body = (c - o)
    rng = (h - l).replace(0.0, np.nan)
    out["candle_body"] = body
    out["candle_body_pct"] = body / rng
    out["upper_wick"] = h - c.combine(o, max)
    out["lower_wick"] = c.combine(o, min) - l
    out["return_1"] = c.pct_change()
    out["volatility_20"] = out["return_1"].rolling(20, min_periods=20).std()

    return out
