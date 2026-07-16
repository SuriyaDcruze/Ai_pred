"""India / NSE-specific features — genuinely different information for equities.

The V2 (India-first) spec asks for signals that matter for NSE stocks specifically,
not generic crypto TA. The star is **relative strength vs the Nifty 50** — "is this
stock beating the index?" — a classic, powerful equity concept that is NOT price-only
and NOT in our existing feature set. If anything India-specific helps where generic
features failed, it is most likely this.

All features are **past-only** (no future candles, no centered windows). The Nifty
series is aligned to the stock's own dates and forward-filled from closed bars only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

INDIA_FEATURES: tuple[str, ...] = (
    "in_rs_nifty_20",     # 20-bar return minus Nifty's 20-bar return (relative strength)
    "in_rs_nifty_50",     # 50-bar relative strength
    "in_beta_proxy",      # stock move / index move (co-movement)
    "in_gap_pct",         # overnight gap vs previous close
    "in_vwap_dist",       # distance from a rolling VWAP proxy, ATR-normalised
    "in_dist_52w_high",   # distance below the ~252-bar high (0 = at high)
    "in_dist_52w_low",    # distance above the ~252-bar low
    "in_weekly_trend",    # sign of the 5-bar (≈1 week on daily) trend
    "in_rel_volume",      # today's volume vs 20-bar average
    "in_close_strength",  # where the close sits in the day's range
)


def add_india_features(df: pd.DataFrame, nifty: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add NSE-specific features. ``nifty`` is the index OHLC aligned by date (optional
    — relative-strength columns are zero-filled if it's unavailable)."""
    out = df.copy()
    o, h, l, c = out["open"], out["high"], out["low"], out["close"]
    v = out.get("volume", pd.Series(1.0, index=out.index))
    atr = out.get("atr", (h - l).rolling(14, min_periods=1).mean()).replace(0.0, np.nan)

    # --- relative strength vs Nifty (the key equity signal) ---
    if nifty is not None and "close" in nifty:
        nclose = nifty["close"].reindex(out.index, method="ffill")
        for w, col in ((20, "in_rs_nifty_20"), (50, "in_rs_nifty_50")):
            stock_ret = c.pct_change(w)
            index_ret = nclose.pct_change(w)
            out[col] = (stock_ret - index_ret).fillna(0.0)
        # beta proxy: co-movement of daily returns over 20 bars (past-only)
        sr = c.pct_change(); ir = nclose.pct_change()
        cov = sr.rolling(20, min_periods=5).cov(ir)
        var = ir.rolling(20, min_periods=5).var().replace(0.0, np.nan)
        out["in_beta_proxy"] = (cov / var).fillna(1.0).clip(-3, 3)
    else:
        out["in_rs_nifty_20"] = 0.0
        out["in_rs_nifty_50"] = 0.0
        out["in_beta_proxy"] = 1.0

    # --- gap behaviour ---
    out["in_gap_pct"] = ((o - c.shift(1)) / c.shift(1)).fillna(0.0) * 100

    # --- VWAP-distance proxy (rolling typical-price VWAP), ATR-normalised ---
    tp = (h + l + c) / 3.0
    roll = 20
    vwap = (tp * v).rolling(roll, min_periods=1).sum() / v.rolling(roll, min_periods=1).sum().replace(0.0, np.nan)
    out["in_vwap_dist"] = ((c - vwap) / atr).fillna(0.0)

    # --- 52-week (≈252 daily bars) position ---
    hi52 = h.rolling(252, min_periods=20).max()
    lo52 = l.rolling(252, min_periods=20).min()
    out["in_dist_52w_high"] = ((c - hi52) / atr).fillna(0.0)   # ≤ 0, 0 = at the high
    out["in_dist_52w_low"] = ((c - lo52) / atr).fillna(0.0)    # ≥ 0

    # --- weekly trend (5 daily bars) ---
    out["in_weekly_trend"] = np.sign(c - c.shift(5)).fillna(0.0)

    # --- relative volume & closing strength ---
    out["in_rel_volume"] = (v / v.rolling(20, min_periods=1).mean()).replace([np.inf, -np.inf], 1.0).fillna(1.0)
    rng = (h - l).replace(0.0, np.nan)
    out["in_close_strength"] = ((c - l) / rng).fillna(0.5)

    return out
