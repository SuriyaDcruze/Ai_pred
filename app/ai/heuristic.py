"""Heuristic fallback predictor.

Produces a :class:`ModelPrediction` from indicators alone, with NO neural net.
Used when a trained checkpoint is absent so the API, demo, and decision engine
are fully exercisable out of the box. It is deliberately conservative and never
reports high confidence — a reminder to train the real model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.data.schemas import ModelPrediction


class HeuristicPredictor:
    """Rule-based scorer standing in for the neural model."""

    def predict(self, ohlcv_or_features: pd.DataFrame) -> ModelPrediction:
        from app.features.engineering import FeatureBuilder

        frame = ohlcv_or_features
        if "adx" not in frame.columns:  # raw OHLCV -> engineer it
            frame = FeatureBuilder().build_frame(frame)
        row = frame.iloc[-1]

        score = 0.0
        if row.get("ema_9", 0) > row.get("ema_21", 0):
            score += 1
        if row.get("ema_21", 0) > row.get("ema_50", 0):
            score += 1
        if row.get("macd_hist", 0) > 0:
            score += 1
        if row.get("rsi", 50) > 50:
            score += 1
        if row.get("supertrend_dir", 0) > 0:
            score += 1
        if row.get("structure_trend", 0) > 0:
            score += 1
        # score in [0, 6] -> tilt probabilities
        tilt = (score - 3) / 3.0  # [-1, 1]
        p_bull = float(np.clip(0.33 + 0.4 * tilt, 0.05, 0.9))
        p_bear = float(np.clip(0.33 - 0.4 * tilt, 0.05, 0.9))
        p_side = float(max(0.05, 1.0 - p_bull - p_bear))
        total = p_bull + p_bear + p_side
        p_bull, p_bear, p_side = p_bull / total, p_bear / total, p_side / total

        last_close = float(row["close"]) if "close" in row else float(frame["close"].iloc[-1])
        atr = float(row.get("atr", last_close * 0.01) or last_close * 0.01)
        drift = tilt * atr
        # Heuristic confidence is intentionally capped below the 0.80 gate.
        confidence = float(np.clip(0.4 + 0.1 * abs(tilt), 0.0, 0.75))

        return ModelPrediction(
            p_bullish=p_bull,
            p_bearish=p_bear,
            p_sideways=p_side,
            predicted_high=last_close + abs(drift) + atr * 0.5,
            predicted_low=last_close - abs(drift) - atr * 0.5,
            predicted_close=last_close + drift,
            expected_volatility=atr / last_close if last_close else 0.0,
            confidence=confidence,
        )
