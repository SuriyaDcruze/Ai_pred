"""Normalized price-action features — the raw shape of recent movement.

Phase 3 of the Accuracy Improvement spec. Everything here is normalised by ATR (or a
ratio) so it generalises across price levels and volatility regimes, and everything
is past-only. These are cheap, dense features that often carry short-horizon signal
the smoothed indicators miss.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_price_action(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    o, h, l, c = out["open"], out["high"], out["low"], out["close"]
    v = out.get("volume", pd.Series(1.0, index=out.index))
    atr = out.get("atr", (h - l).rolling(14, min_periods=1).mean()).replace(0.0, np.nan)
    rng = (h - l).replace(0.0, np.nan)

    # --- multi-horizon returns (past-only) ---
    for k in (1, 2, 3, 6, 12, 24):
        out[f"pa_return_{k}"] = c.pct_change(k).fillna(0.0)

    # --- candle geometry, ATR-normalised ---
    body = c - o
    out["pa_body_atr"] = (body / atr).fillna(0.0)
    out["pa_abs_body_atr"] = (body.abs() / atr).fillna(0.0)
    out["pa_upper_wick_atr"] = ((h - c.combine(o, max)) / atr).fillna(0.0)
    out["pa_lower_wick_atr"] = ((c.combine(o, min) - l) / atr).fillna(0.0)
    out["pa_range_atr"] = (rng / atr).fillna(0.0)
    # close location within the candle: 1 = closed at high, 0 = at low
    out["pa_close_loc"] = ((c - l) / rng).fillna(0.5)

    # --- distance from recent highs/lows, ATR-normalised ---
    for w in (20, 50):
        hi = h.rolling(w, min_periods=1).max()
        lo = l.rolling(w, min_periods=1).min()
        out[f"pa_dist_high_{w}_atr"] = ((c - hi) / atr).fillna(0.0)
        out[f"pa_dist_low_{w}_atr"] = ((c - lo) / atr).fillna(0.0)

    # --- swing structure counts (past-only) ---
    up_bar = (c > c.shift(1))
    dn_bar = (c < c.shift(1))
    out["pa_consec_bull"] = up_bar.groupby((~up_bar).cumsum()).cumcount().where(up_bar, 0).astype(float)
    out["pa_consec_bear"] = dn_bar.groupby((~dn_bar).cumsum()).cumcount().where(dn_bar, 0).astype(float)

    # --- momentum shape ---
    m_short = c.pct_change(3)
    m_med = c.pct_change(12)
    out["pa_mom_short"] = m_short.fillna(0.0)
    out["pa_mom_med"] = m_med.fillna(0.0)
    out["pa_mom_diff"] = (m_short - m_med).fillna(0.0)
    out["pa_return_accel"] = c.pct_change(1).diff().fillna(0.0)

    # --- volume behaviour ---
    out["pa_vol_change_1"] = v.pct_change(1).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    rv20 = (v / v.rolling(20, min_periods=1).mean()).replace([np.inf, -np.inf], 1.0)
    out["pa_rel_volume_20"] = rv20.fillna(1.0)
    vmean = v.rolling(20, min_periods=5).mean()
    vstd = v.rolling(20, min_periods=5).std().replace(0.0, np.nan)
    out["pa_volume_z"] = ((v - vmean) / vstd).fillna(0.0)
    # price/volume confirmation: same-sign move + above-average volume
    out["pa_pv_confirm"] = (np.sign(body) * (rv20 - 1.0)).fillna(0.0)

    return out


PRICE_ACTION_FEATURES: tuple[str, ...] = (
    "pa_return_1", "pa_return_2", "pa_return_3", "pa_return_6", "pa_return_12", "pa_return_24",
    "pa_body_atr", "pa_abs_body_atr", "pa_upper_wick_atr", "pa_lower_wick_atr",
    "pa_range_atr", "pa_close_loc",
    "pa_dist_high_20_atr", "pa_dist_low_20_atr", "pa_dist_high_50_atr", "pa_dist_low_50_atr",
    "pa_consec_bull", "pa_consec_bear",
    "pa_mom_short", "pa_mom_med", "pa_mom_diff", "pa_return_accel",
    "pa_vol_change_1", "pa_rel_volume_20", "pa_volume_z", "pa_pv_confirm",
)
