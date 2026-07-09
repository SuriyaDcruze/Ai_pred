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
    deadband_k: float = 0.5,
    vol_window: int = 20,
) -> dict[str, np.ndarray]:
    """Return causal supervised targets aligned to each bar ``t``.

    ``deadband_k`` scales the sideways band by recent volatility: a move smaller
    than ``deadband_k * rolling_std`` is labelled sideways rather than up/down.
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

        band = deadband_k * (roll_std[t] if not np.isnan(roll_std[t]) else 0.0)
        if rc > band:
            direction[t] = 0
        elif rc < -band:
            direction[t] = 1
        else:
            direction[t] = 2

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
