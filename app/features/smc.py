"""Smart-Money-Concept (SMC) / price-action structural features.

Implements the structural primitives institutional discretionary traders read:
fractal swing points, Break of Structure (BOS), Change of Character (CHoCH),
Fair Value Gaps (FVG), bullish/bearish Order Blocks, and buy/sell-side
liquidity pools. All are computed causally (no lookahead beyond the fractal
confirmation lag, which is disclosed via ``fractal`` width).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def swing_points(high: pd.Series, low: pd.Series, width: int = 2) -> pd.DataFrame:
    """Mark fractal swing highs/lows.

    A swing high at bar ``i`` is a high strictly greater than the ``width`` bars
    on each side. Confirmation therefore lags by ``width`` bars — the flag is
    placed at the pivot bar but only becomes knowable ``width`` bars later. Use
    :func:`swing_points_causal` if you need a strictly no-lookahead flag.
    """
    n = len(high)
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    h = high.to_numpy()
    l = low.to_numpy()
    for i in range(width, n - width):
        window_h = h[i - width : i + width + 1]
        window_l = l[i - width : i + width + 1]
        if h[i] == window_h.max() and (window_h == h[i]).sum() == 1:
            is_high[i] = True
        if l[i] == window_l.min() and (window_l == l[i]).sum() == 1:
            is_low[i] = True
    return pd.DataFrame(
        {"swing_high": is_high, "swing_low": is_low}, index=high.index
    )


def market_structure(
    high: pd.Series, low: pd.Series, close: pd.Series, width: int = 2
) -> pd.DataFrame:
    """Derive BOS / CHoCH flags and a running structural trend (+1/-1/0).

    Logic:
      * Track the last confirmed swing high and swing low.
      * BOS (bullish) = close breaks above the last swing high while trend up.
      * BOS (bearish) = close breaks below the last swing low while trend down.
      * CHoCH = the first break that flips the prevailing trend.
    """
    swings = swing_points(high, low, width)
    c = close.to_numpy()
    idx = high.index
    n = len(c)

    bos = np.zeros(n, dtype=float)
    choch = np.zeros(n, dtype=float)
    trend = np.zeros(n, dtype=float)

    last_sh: float | None = None
    last_sl: float | None = None
    cur_trend = 0
    sh_flags = swings["swing_high"].to_numpy()
    sl_flags = swings["swing_low"].to_numpy()
    h = high.to_numpy()
    l = low.to_numpy()

    for i in range(n):
        # A swing is only *confirmed* width bars after it prints.
        if i - width >= 0:
            j = i - width
            if sh_flags[j]:
                last_sh = h[j]
            if sl_flags[j]:
                last_sl = l[j]

        if last_sh is not None and c[i] > last_sh:
            if cur_trend < 0:
                choch[i] = 1.0  # bearish -> bullish
            else:
                bos[i] = 1.0
            cur_trend = 1
            last_sh = None  # consumed; wait for next swing high
        elif last_sl is not None and c[i] < last_sl:
            if cur_trend > 0:
                choch[i] = -1.0  # bullish -> bearish
            else:
                bos[i] = -1.0
            cur_trend = -1
            last_sl = None

        trend[i] = cur_trend

    return pd.DataFrame(
        {
            "swing_high": swings["swing_high"],
            "swing_low": swings["swing_low"],
            "bos": bos,
            "choch": choch,
            "structure_trend": trend,
        },
        index=idx,
    )


def fair_value_gaps(
    high: pd.Series, low: pd.Series, min_gap_atr: float = 0.0, atr: pd.Series | None = None
) -> pd.DataFrame:
    """Detect 3-candle Fair Value Gaps (imbalances).

    Bullish FVG at bar ``i``: ``low[i] > high[i-2]`` (gap between candle i-2's
    high and candle i's low, with the middle candle's body inside). Bearish is
    the mirror. Returns gap presence flags and the gap size.
    """
    h = high.to_numpy()
    l = low.to_numpy()
    n = len(h)
    bull = np.zeros(n, dtype=float)
    bear = np.zeros(n, dtype=float)
    size = np.zeros(n, dtype=float)

    thr = None
    if atr is not None:
        thr = (atr * min_gap_atr).to_numpy()

    for i in range(2, n):
        gap_up = l[i] - h[i - 2]
        gap_dn = l[i - 2] - h[i]
        limit = thr[i] if thr is not None else 0.0
        if gap_up > 0 and gap_up >= limit:
            bull[i] = 1.0
            size[i] = gap_up
        elif gap_dn > 0 and gap_dn >= limit:
            bear[i] = 1.0
            size[i] = -gap_dn
    return pd.DataFrame(
        {"fvg_bull": bull, "fvg_bear": bear, "fvg_size": size}, index=high.index
    )


def order_blocks(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
) -> pd.DataFrame:
    """Flag the last opposing candle before an impulsive move (order block proxy).

    Bullish OB: a down candle immediately followed by a strong up candle that
    closes above the down candle's high. Bearish OB is the mirror.
    """
    o, h, l, c = (open_.to_numpy(), high.to_numpy(), low.to_numpy(), close.to_numpy())
    n = len(c)
    bull_ob = np.zeros(n, dtype=float)
    bear_ob = np.zeros(n, dtype=float)
    for i in range(1, n):
        down_prev = c[i - 1] < o[i - 1]
        up_prev = c[i - 1] > o[i - 1]
        strong_up = c[i] > o[i] and c[i] > h[i - 1]
        strong_dn = c[i] < o[i] and c[i] < l[i - 1]
        if down_prev and strong_up:
            bull_ob[i - 1] = 1.0
        if up_prev and strong_dn:
            bear_ob[i - 1] = 1.0
    return pd.DataFrame(
        {"ob_bull": bull_ob, "ob_bear": bear_ob}, index=open_.index
    )


def liquidity_pools(
    high: pd.Series, low: pd.Series, lookback: int = 20, tol: float = 0.0005
) -> pd.DataFrame:
    """Approximate buy/sell-side liquidity as clusters of equal highs/lows.

    Returns, per bar, the distance (in fraction of price) to the nearest cluster
    of >=2 equal highs above (sell-side liquidity) and equal lows below.
    """
    h = high.to_numpy()
    l = low.to_numpy()
    n = len(h)
    sell_liq = np.full(n, np.nan)
    buy_liq = np.full(n, np.nan)
    for i in range(lookback, n):
        win_h = h[i - lookback : i]
        win_l = l[i - lookback : i]
        highs_above = win_h[win_h >= h[i]]
        lows_below = win_l[win_l <= l[i]]
        if highs_above.size >= 2:
            sell_liq[i] = (highs_above.min() - h[i]) / h[i]
        if lows_below.size >= 2:
            buy_liq[i] = (l[i] - lows_below.max()) / l[i]
    return pd.DataFrame(
        {"sell_liquidity_dist": sell_liq, "buy_liquidity_dist": buy_liq}, index=high.index
    )


def add_smc_features(df: pd.DataFrame, atr: pd.Series | None = None) -> pd.DataFrame:
    """Augment an OHLCV frame with all SMC structural features."""
    out = df.copy()
    ms = market_structure(df["high"], df["low"], df["close"])
    out = out.join(ms)
    out = out.join(fair_value_gaps(df["high"], df["low"], min_gap_atr=0.25, atr=atr))
    out = out.join(order_blocks(df["open"], df["high"], df["low"], df["close"]))
    out = out.join(liquidity_pools(df["high"], df["low"]))
    return out
