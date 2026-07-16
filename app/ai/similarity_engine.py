"""Historical Similarity Engine — "your setup looks like these past ones."

Phase 5's one genuinely testable idea. For a new setup it finds the k most similar
*past* situations (nearest neighbours in standardised feature space) and reports how
trades actually turned out in those situations: win rate, average R, count.

Two honest uses, in priority order:
  1. **Explainability** — the real win. It lets the platform say, in plain English,
     "this setup resembles 20 past ones that won 63% at +0.31R." That is trust you can
     inspect, not a black-box score.
  2. **A candidate feature** — we also test whether feeding the neighbour win-rate to
     the outcome model improves expectancy. Expectation: little, because kNN answers
     the same question the gradient-boosted outcome model already answers, from the
     same features. We measure it rather than assume.

Leakage rule: neighbours are drawn ONLY from history strictly before the query bar.
The engine is fit on the training slice; queries use that fitted history only.
"""

from __future__ import annotations

import numpy as np


class SimilarityEngine:
    """kNN over historical setups → outcome statistics of the neighbours."""

    def __init__(self, k: int = 20):
        self.k = k
        self._mean = None
        self._std = None
        self._X = None          # standardised historical features
        self._won = None        # 1 if that historical trade hit target first
        self._r = None          # realised R of that historical trade

    def fit(self, X_hist: np.ndarray, won_hist: np.ndarray, r_hist: np.ndarray) -> "SimilarityEngine":
        self._mean = X_hist.mean(axis=0)
        self._std = X_hist.std(axis=0)
        self._std[self._std == 0] = 1.0
        self._X = (X_hist - self._mean) / self._std
        self._won = won_hist.astype(float)
        self._r = r_hist.astype(float)
        return self

    def query(self, x: np.ndarray) -> dict:
        """Nearest-neighbour outcome stats for a single setup ``x`` (raw features)."""
        if self._X is None or len(self._X) < self.k:
            return {"n": 0, "win_rate": float("nan"), "avg_R": float("nan"), "similarity": float("nan")}
        xs = (x - self._mean) / self._std
        d = np.sqrt(((self._X - xs) ** 2).sum(axis=1))
        idx = np.argpartition(d, self.k)[: self.k]
        # similarity in (0,1]: 1 for an identical neighbour, decaying with distance
        sim = float(np.mean(1.0 / (1.0 + d[idx])))
        return {
            "n": int(self.k),
            "win_rate": float(self._won[idx].mean()),
            "avg_R": float(self._r[idx].mean()),
            "similarity": sim,
        }

    def query_batch(self, X: np.ndarray) -> np.ndarray:
        """Neighbour win-rate for many rows → an (N,) array. For feature-testing."""
        out = np.full(len(X), np.nan)
        if self._X is None or len(self._X) < self.k:
            return out
        Xs = (X - self._mean) / self._std
        for i in range(len(Xs)):
            d = np.sqrt(((self._X - Xs[i]) ** 2).sum(axis=1))
            idx = np.argpartition(d, self.k)[: self.k]
            out[i] = self._won[idx].mean()
        return out
