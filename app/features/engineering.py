"""Feature assembly: raw OHLCV → clean, scaled model-ready matrix.

``FeatureBuilder`` is the single entry point used by both training and live
inference so the two paths can never drift out of sync. It:

  1. adds technical indicators,
  2. adds SMC structural features,
  3. selects the model feature set,
  4. handles NaNs (warm-up rows dropped or filled),
  5. optionally scales using persisted statistics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.indicators.technical import add_all_indicators
from app.features.smc import add_smc_features
from app.features.candlesticks import (
    CANDLE_FEATURE_COLUMNS,
    add_candlestick_patterns,
    candlestick_confluence,
)


# Columns fed to the network. Prices are converted to returns/relative form so
# the model generalises across absolute price levels.
RAW_FEATURE_COLUMNS: tuple[str, ...] = (
    "return_1",
    "volatility_20",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "stoch_k",
    "stoch_d",
    "cci",
    "roc",
    "mfi",
    "atr",
    "bb_width",
    "adx",
    "plus_di",
    "minus_di",
    "supertrend_dir",
    "obv",
    "volume_delta",
    "candle_body_pct",
    "upper_wick",
    "lower_wick",
    "structure_trend",
    "bos",
    "choch",
    "fvg_bull",
    "fvg_bear",
    "ob_bull",
    "ob_bear",
    # Candlestick patterns (from The Candlestick Trading Bible)
    *CANDLE_FEATURE_COLUMNS,
    "cdl_confluence",
)


@dataclass
class FeatureBuilder:
    """Builds and (optionally) scales the model feature matrix.

    Fit scaler stats on the training set with :meth:`fit`, persist them alongside
    the model, then reuse via :meth:`transform` at inference time.
    """

    feature_columns: tuple[str, ...] = RAW_FEATURE_COLUMNS
    mean_: np.ndarray | None = field(default=None)
    std_: np.ndarray | None = field(default=None)

    def build_frame(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Return the engineered feature frame (indicators + SMC), no scaling."""
        enriched = add_all_indicators(ohlcv)
        enriched = add_smc_features(enriched, atr=enriched.get("atr"))
        enriched = add_candlestick_patterns(enriched)
        # The book's "pattern + location + trend" confluence score.
        enriched["cdl_confluence"] = candlestick_confluence(enriched)
        return enriched

    def _matrix(self, frame: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in self.feature_columns if c not in frame.columns]
        if missing:
            raise KeyError(f"FeatureBuilder: engineered frame missing {missing}")
        mat = frame[list(self.feature_columns)].copy()
        # Warm-up NaNs at the head; forward-fill structural flags, drop the rest.
        mat = mat.replace([np.inf, -np.inf], np.nan)
        mat = mat.ffill().fillna(0.0)
        return mat

    def fit(self, ohlcv: pd.DataFrame) -> "FeatureBuilder":
        mat = self._matrix(self.build_frame(ohlcv))
        self.mean_ = mat.mean().to_numpy()
        self.std_ = mat.std(ddof=0).replace(0.0, 1.0).to_numpy()
        return self

    def transform(self, ohlcv: pd.DataFrame) -> np.ndarray:
        """Return a scaled ``(T, F)`` float32 array aligned to ``ohlcv``'s index."""
        mat = self._matrix(self.build_frame(ohlcv))
        arr = mat.to_numpy(dtype=np.float32)
        if self.mean_ is not None and self.std_ is not None:
            arr = (arr - self.mean_) / self.std_
        return arr.astype(np.float32)

    def fit_transform(self, ohlcv: pd.DataFrame) -> np.ndarray:
        return self.fit(ohlcv).transform(ohlcv)

    @property
    def n_features(self) -> int:
        return len(self.feature_columns)

    def state_dict(self) -> dict:
        return {
            "feature_columns": list(self.feature_columns),
            "mean": None if self.mean_ is None else self.mean_.tolist(),
            "std": None if self.std_ is None else self.std_.tolist(),
        }

    @classmethod
    def from_state_dict(cls, state: dict) -> "FeatureBuilder":
        fb = cls(feature_columns=tuple(state["feature_columns"]))
        if state.get("mean") is not None:
            fb.mean_ = np.asarray(state["mean"], dtype=np.float32)
            fb.std_ = np.asarray(state["std"], dtype=np.float32)
        return fb
