"""Market-regime features — tell the model *what kind of market* it's in.

Phase 2 of the Accuracy Improvement spec. The idea: the same signal means different
things in a strong trend vs. a dead sideways chop. Giving the model explicit
trend/volatility context lets it condition on regime instead of averaging over all
of them. This is one of the few honest levers on directional accuracy.

**Every feature here uses only past and current candles** — no negative shifts, no
centered rolling windows. A future-invariance test verifies that changing a future
candle never changes an earlier feature — the one property that matters.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _slope(series: pd.Series, period: int) -> pd.Series:
    """Rolling linear slope (per-bar), normalised — past-only."""
    return (series - series.shift(period)) / period


def add_market_regime(df: pd.DataFrame, *, adx_strong: float = 25.0, adx_weak: float = 18.0,
                      vol_lookback: int = 100) -> pd.DataFrame:
    """Add trend/volatility regime features. Assumes indicators already present
    (adx, atr, ema_20/ema_50 — added by `add_all_indicators`)."""
    out = df.copy()
    close = out["close"]
    atr = out.get("atr", pd.Series(np.nan, index=out.index))
    atr_safe = atr.replace(0.0, np.nan)

    ema_fast = out.get("ema_21", close.ewm(span=20, adjust=False).mean())
    ema_slow = out.get("ema_50", close.ewm(span=50, adjust=False).mean())
    adx = out.get("adx", pd.Series(0.0, index=out.index))

    # --- trend direction & strength ---
    out["reg_ema_gap_atr"] = ((ema_fast - ema_slow) / atr_safe).fillna(0.0)
    out["reg_ema20_slope"] = (_slope(ema_fast, 5) / atr_safe).fillna(0.0)
    out["reg_ema50_slope"] = (_slope(ema_slow, 10) / atr_safe).fillna(0.0)
    out["reg_dist_ema20_atr"] = ((close - ema_fast) / atr_safe).fillna(0.0)
    out["reg_dist_ema50_atr"] = ((close - ema_slow) / atr_safe).fillna(0.0)

    up = out["reg_ema_gap_atr"] > 0
    trending = adx >= adx_weak
    strong = adx >= adx_strong
    # trend_regime: +2 strong up, +1 weak up, -1 weak down, -2 strong down, 0 sideways
    trend_regime = pd.Series(0, index=out.index)
    trend_regime = trend_regime.mask(trending & up & strong, 2)
    trend_regime = trend_regime.mask(trending & up & ~strong, 1)
    trend_regime = trend_regime.mask(trending & ~up & ~strong, -1)
    trend_regime = trend_regime.mask(trending & ~up & strong, -2)
    out["reg_trend"] = trend_regime.astype(float)
    out["reg_adx"] = adx.fillna(0.0)

    # --- volatility regime (percentile of ATR% over a trailing window) ---
    atr_pct = (atr / close).fillna(0.0)
    out["reg_atr_pct"] = atr_pct
    # trailing percentile rank — expanding-safe, past-only via rolling apply
    out["reg_atr_percentile"] = (
        atr_pct.rolling(vol_lookback, min_periods=20)
        .apply(lambda w: (w[-1] >= w).mean(), raw=True)
        .fillna(0.5)
    )
    bbw = out.get("bb_width", pd.Series(np.nan, index=out.index))
    out["reg_bbw_percentile"] = (
        bbw.rolling(vol_lookback, min_periods=20)
        .apply(lambda w: (w[-1] >= w).mean(), raw=True)
        .fillna(0.5)
    )

    # --- trend persistence: how consistently price has risen/fallen ---
    ret_sign = np.sign(close.diff()).fillna(0.0)
    out["reg_trend_persistence"] = ret_sign.rolling(20, min_periods=5).mean().fillna(0.0)

    return out


REGIME_FEATURES: tuple[str, ...] = (
    "reg_ema_gap_atr", "reg_ema20_slope", "reg_ema50_slope",
    "reg_dist_ema20_atr", "reg_dist_ema50_atr",
    "reg_trend", "reg_adx",
    "reg_atr_pct", "reg_atr_percentile", "reg_bbw_percentile",
    "reg_trend_persistence",
)
