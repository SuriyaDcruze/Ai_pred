"""Feature-interaction engine — products of features, for the *linear* model.

Phase-2 spec's Improvement 4, and it's genuinely well-motivated **specifically because
our champion is logistic regression.** A linear model cannot represent "high ADX AND
high volume" on its own — it only sums individual features. Handing it the *product*
ADX×Volume lets it express a joint condition it otherwise can't. (A tree model gets
interactions for free; a linear one does not, which is exactly why this is worth a try
here and wasn't for the deep net.)

Each interaction is the product of two already-computed, roughly-normalised features.
No raw prices, no future data — these are pure functions of the current feature row,
so they inherit the leakage-safety of their inputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# (name, feature_a, feature_b) — a small, hand-picked set. NOT every combination:
# the spec is explicit that spraying all products invites overfitting.
_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("ix_adx_atr", "adx", "atr"),
    ("ix_adx_voldelta", "adx", "volume_delta"),
    ("ix_rsi_bbwidth", "rsi", "bb_width"),
    ("ix_macdhist_adx", "macd_hist", "adx"),
    ("ix_rsi_voldelta", "rsi", "volume_delta"),
    ("ix_roc_adx", "roc", "adx"),
    ("ix_stochk_rsi", "stoch_k", "rsi"),
    ("ix_atr_voldelta", "atr", "volume_delta"),
)


def add_interactions(df: pd.DataFrame) -> pd.DataFrame:
    """Add pairwise interaction features. Missing inputs → zero column (stable set)."""
    out = df.copy()
    for name, a, b in _PAIRS:
        if a in out.columns and b in out.columns:
            va = out[a].replace([np.inf, -np.inf], np.nan).fillna(0.0)
            vb = out[b].replace([np.inf, -np.inf], np.nan).fillna(0.0)
            out[name] = (va * vb).to_numpy()
        else:
            out[name] = 0.0
    return out


INTERACTION_FEATURES: tuple[str, ...] = tuple(name for name, _, _ in _PAIRS)
