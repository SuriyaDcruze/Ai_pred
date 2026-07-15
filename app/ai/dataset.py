"""Windowed time-series dataset and causal label generation.

Given an engineered feature matrix ``X`` (T, F) and the raw close/high/low, we
build supervised windows of length ``seq_len``. The label for a window ending at
bar ``t`` describes bar ``t+horizon`` — strictly in the future — so there is no
target leakage.

Targets (all relative to close[t]):
  * direction: 0 bull / 1 bear / 2 sideways, using a volatility-scaled deadband
  * rel_high  = high[t+1..t+h].max() / close[t] - 1
  * rel_low   = low[t+1..t+h].min()  / close[t] - 1
  * rel_close = close[t+h] / close[t] - 1
  * volatility = realized std of returns over the horizon
"""

from __future__ import annotations

import numpy as np
import torch
from torch.utils.data import Dataset


def make_labels(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    horizon: int,
    barrier_k: float = 1.0,
    vol_window: int = 20,
    deadband_k: float = 0.5,  # kept for backward compat; unused by triple-barrier
    cost_pct: float = 0.0,
) -> dict[str, np.ndarray]:
    """Return causal supervised targets aligned to each bar ``t``.

    Direction uses the **triple-barrier method** (López de Prado): set an upper
    and lower barrier ``barrier_k × recent-volatility`` away from the entry, then
    look forward up to ``horizon`` bars and label by which barrier price touches
    *first* — up (0), down (1), or neither/time-out (2). This matches the real
    trading question ("does it hit my target before my stop?") and is far more
    learnable than a single next-candle up/down flip.

    ``cost_pct`` makes the labels **cost-aware**, per the MASTER MODEL CONTEXT:

        "A movement smaller than total estimated trading costs should not
         automatically be labeled as a profitable directional opportunity."

    Without it, in a quiet market the volatility barrier can sit *inside* the
    spread+fees, and we cheerfully label a 0.02% wiggle as a tradeable UP. The
    model then learns to predict moves it is impossible to profit from — it is
    being trained to chase noise, and it will look accurate while losing money.
    Setting ``cost_pct`` (round-trip fees + slippage, e.g. 0.0012 = 0.12%) floors
    the barrier at the cost of trading, so only moves worth taking are labelled
    directional. Everything else is NEUTRAL, which is the honest answer.
    """
    n = len(close)
    rel_close = np.full(n, np.nan)
    rel_high = np.full(n, np.nan)
    rel_low = np.full(n, np.nan)
    realized_vol = np.full(n, np.nan)
    direction = np.full(n, -1, dtype=np.int64)

    log_ret = np.diff(np.log(close), prepend=np.log(close[0]))
    roll_std = _rolling_std(log_ret, vol_window)

    for t in range(n - horizon):
        c0 = close[t]
        fut_close = close[t + horizon]
        fut_high = high[t + 1 : t + horizon + 1].max()
        fut_low = low[t + 1 : t + horizon + 1].min()
        rc = fut_close / c0 - 1.0
        rel_close[t] = rc
        rel_high[t] = fut_high / c0 - 1.0
        rel_low[t] = fut_low / c0 - 1.0
        realized_vol[t] = np.std(log_ret[t + 1 : t + horizon + 1]) if horizon > 1 else abs(rc)

        # --- Triple-barrier direction label ---
        vol = roll_std[t] if not np.isnan(roll_std[t]) else 0.0
        b = barrier_k * vol
        # Cost floor: a barrier tighter than the round-trip cost describes a move
        # you cannot profit from, so never label it as one.
        b = max(b, cost_pct)
        if b <= 0:
            direction[t] = 2
            continue
        upper, lower = c0 * (1 + b), c0 * (1 - b)
        label = 2  # neither barrier hit within the horizon (time-out)
        for j in range(t + 1, t + horizon + 1):
            hit_up = high[j] >= upper
            hit_dn = low[j] <= lower
            if hit_up and hit_dn:
                label = 2  # both in one bar → ambiguous
                break
            if hit_up:
                label = 0  # up-target hit first
                break
            if hit_dn:
                label = 1  # down-target hit first
                break
        direction[t] = label

    return {
        "direction": direction,
        "rel_close": rel_close,
        "rel_high": rel_high,
        "rel_low": rel_low,
        "volatility": realized_vol,
    }


def _rolling_std(x: np.ndarray, window: int) -> np.ndarray:
    s = np.full_like(x, np.nan, dtype=float)
    for i in range(window, len(x)):
        s[i] = x[i - window : i].std()
    return s


class SequenceDataset(Dataset):
    """Sliding-window dataset yielding ``(window, targets)`` pairs.

    Windows whose label horizon runs past the end of the series are dropped.
    """

    def __init__(
        self,
        features: np.ndarray,   # (T, F) already scaled
        labels: dict[str, np.ndarray],
        seq_len: int,
        horizon: int,
    ):
        self.features = features.astype(np.float32)
        self.labels = labels
        self.seq_len = seq_len
        self.horizon = horizon
        t = len(features)
        # last valid window end = t - horizon - 1 ; need seq_len history before it
        self.indices = [
            end for end in range(seq_len - 1, t - horizon)
            if labels["direction"][end] >= 0
        ]

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, i: int):
        end = self.indices[i]
        start = end - self.seq_len + 1
        window = torch.from_numpy(self.features[start : end + 1])
        target = {
            "direction": torch.tensor(self.labels["direction"][end], dtype=torch.long),
            "prices": torch.tensor(
                [self.labels["rel_high"][end], self.labels["rel_low"][end], self.labels["rel_close"][end]],
                dtype=torch.float32,
            ),
            "volatility": torch.tensor(self.labels["volatility"][end], dtype=torch.float32),
        }
        return window, target
