"""Multi-timeframe feature fusion — higher-timeframe context for a lower-TF model.

Phase-2 spec's #1 priority. A 1h model that also *sees* the 4h and 1d trend has more
context than one staring at 1h alone. The catch — and the whole difficulty — is
**leakage**: at time t, the 4h candle that *contains* t has not closed yet, so using
it would be reading the future.

The leakage-safe recipe here:
  1. resample the base series to each higher timeframe, timestamped at bar **close**
     (`label="right", closed="right"`),
  2. compute simple, robust context features on the higher-TF series,
  3. **shift by one higher-TF bar** so only *fully closed* higher-TF candles are used,
  4. reindex onto the base index with forward-fill (past values only — never future).

The future-invariance test in `test_feature_leakage.py`-style covers this: corrupt
future candles, confirm earlier multi-TF features are unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# (higher timeframe, pandas resample rule). Kept small — more TFs = more noise.
_HTF_RULES = {"4h": "4h", "1d": "1D"}


def _htf_context(base: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Compute leakage-safe higher-timeframe context aligned to the base index."""
    agg = base.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    if len(agg) < 30:
        return pd.DataFrame(index=base.index)

    c = agg["close"]
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    atr = (agg["high"] - agg["low"]).rolling(14, min_periods=1).mean().replace(0.0, np.nan)

    feats = pd.DataFrame(index=agg.index)
    feats["htf_ret_1"] = c.pct_change(1).fillna(0.0)
    feats["htf_ret_3"] = c.pct_change(3).fillna(0.0)
    feats["htf_ema_gap"] = ((ema20 - ema50) / atr).fillna(0.0)
    feats["htf_ema20_slope"] = ((ema20 - ema20.shift(3)) / atr).fillna(0.0)
    feats["htf_dist_ema20"] = ((c - ema20) / atr).fillna(0.0)
    feats["htf_trend_up"] = (ema20 > ema50).astype(float)
    # trend persistence: fraction of last 10 HTF bars that closed up
    feats["htf_persistence"] = np.sign(c.diff()).rolling(10, min_periods=3).mean().fillna(0.0)

    # CRITICAL: shift by one closed HTF bar, then ffill onto the base index. This
    # guarantees a base bar at time t only sees HTF bars that closed at or before t.
    feats = feats.shift(1)
    aligned = feats.reindex(base.index, method="ffill")
    return aligned.add_prefix(f"{rule.lower()}_")


def add_multi_timeframe(df: pd.DataFrame) -> pd.DataFrame:
    """Augment a base (e.g. 1h) frame with 4h and 1d context features."""
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        for col in MULTI_TF_FEATURES:
            out[col] = 0.0
        return out
    for _, rule in _HTF_RULES.items():
        ctx = _htf_context(df, rule)
        for col in ctx.columns:
            out[col] = ctx[col].fillna(0.0).to_numpy()
    # ensure a stable column set even if a TF had too little data
    for col in MULTI_TF_FEATURES:
        if col not in out.columns:
            out[col] = 0.0
    return out


_BASE_COLS = ("htf_ret_1", "htf_ret_3", "htf_ema_gap", "htf_ema20_slope",
              "htf_dist_ema20", "htf_trend_up", "htf_persistence")
MULTI_TF_FEATURES: tuple[str, ...] = tuple(
    f"{rule.lower()}_{c}" for rule in _HTF_RULES.values() for c in _BASE_COLS
)
