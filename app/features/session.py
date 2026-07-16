"""Time & market-session features — when a bar happens can matter.

Phase 4 of the Accuracy Improvement spec. Liquidity and volatility follow the clock:
crypto has Asian / London / New York sessions and weekend lulls; stocks have an
open, midday, and close. Cyclical (sin/cos) encoding means hour 23 and hour 0 are
neighbours, not opposite extremes.

All timestamps are treated as UTC (our candles are tz-aware UTC). Session windows
are approximate UTC hours — good enough to capture the regime, and honest about it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_session_features(df: pd.DataFrame, *, is_stock: bool = False) -> pd.DataFrame:
    out = df.copy()
    idx = out.index
    if not isinstance(idx, pd.DatetimeIndex):
        # no usable timestamps → emit zeros so the column set stays stable
        for col in SESSION_FEATURES:
            out[col] = 0.0
        return out

    hour = idx.hour.to_numpy(dtype=float)
    dow = idx.dayofweek.to_numpy(dtype=float)   # 0 = Monday

    # cyclical encodings — 24h and 7d wrap around cleanly
    out["ses_hour_sin"] = np.sin(2 * np.pi * hour / 24)
    out["ses_hour_cos"] = np.cos(2 * np.pi * hour / 24)
    out["ses_dow_sin"] = np.sin(2 * np.pi * dow / 7)
    out["ses_dow_cos"] = np.cos(2 * np.pi * dow / 7)
    out["ses_is_weekend"] = ((dow >= 5).astype(float))

    if is_stock:
        # US cash session ~ 13:30–20:00 UTC. Approximate minute offsets.
        mins = hour * 60 + idx.minute.to_numpy(dtype=float)
        open_m, close_m = 13 * 60 + 30, 20 * 60
        span = max(close_m - open_m, 1)
        frac = np.clip((mins - open_m) / span, 0.0, 1.0)
        in_session = (mins >= open_m) & (mins <= close_m)
        out["ses_from_open"] = np.where(in_session, frac, 0.0)
        out["ses_until_close"] = np.where(in_session, 1.0 - frac, 0.0)
        out["ses_is_open_hour"] = ((frac <= 0.15) & in_session).astype(float)
        out["ses_is_close_hour"] = ((frac >= 0.85) & in_session).astype(float)
        # crypto-session columns zeroed for stocks (keep column set stable)
        for col in ("ses_asian", "ses_london", "ses_newyork", "ses_ln_ny_overlap"):
            out[col] = 0.0
    else:
        out["ses_asian"] = (((hour >= 0) & (hour < 8)).astype(float))
        out["ses_london"] = (((hour >= 7) & (hour < 16)).astype(float))
        out["ses_newyork"] = (((hour >= 13) & (hour < 21)).astype(float))
        out["ses_ln_ny_overlap"] = (((hour >= 13) & (hour < 16)).astype(float))
        for col in ("ses_from_open", "ses_until_close", "ses_is_open_hour", "ses_is_close_hour"):
            out[col] = 0.0

    return out


SESSION_FEATURES: tuple[str, ...] = (
    "ses_hour_sin", "ses_hour_cos", "ses_dow_sin", "ses_dow_cos", "ses_is_weekend",
    "ses_asian", "ses_london", "ses_newyork", "ses_ln_ny_overlap",
    "ses_from_open", "ses_until_close", "ses_is_open_hour", "ses_is_close_hour",
)
