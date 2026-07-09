"""Inference wrapper: load a trained checkpoint and turn a live OHLCV frame into
a :class:`ModelPrediction`. Keeps the exact FeatureBuilder used at train time so
scaling is identical.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import torch

from app.ai.model import HybridTradingModel, ModelConfig
from app.config import settings
from app.data.schemas import ModelPrediction
from app.features.engineering import FeatureBuilder
from app.utils.logging import get_logger

logger = get_logger(__name__)


class Predictor:
    def __init__(self, model: HybridTradingModel, feature_builder: FeatureBuilder, device: str):
        self.model = model.to(device).eval()
        self.fb = feature_builder
        self.device = device

    @classmethod
    def load(cls, path: str | None = None, device: str | None = None) -> "Predictor":
        path = path or settings.model_path
        device = device or settings.resolve_device()
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No model checkpoint at {path!r}. Train one with "
                f"`python -m app.training.train` first."
            )
        ckpt = torch.load(path, map_location=device)
        cfg = ModelConfig(**ckpt["model_config"])
        model = HybridTradingModel(cfg)
        model.load_state_dict(ckpt["model_state"])
        fb = FeatureBuilder.from_state_dict(ckpt["feature_state"])
        logger.info("Loaded model (%s params) from %s", f"{model.num_parameters():,}", path)
        return cls(model, fb, device)

    @torch.no_grad()
    def predict(self, ohlcv: pd.DataFrame) -> ModelPrediction:
        """Predict from the most recent ``seq_len`` candles of ``ohlcv``."""
        seq_len = self.model.cfg.seq_len
        if len(ohlcv) < seq_len:
            raise ValueError(f"Need >= {seq_len} candles, got {len(ohlcv)}")

        feats = self.fb.transform(ohlcv)
        window = feats[-seq_len:]
        x = torch.from_numpy(window).unsqueeze(0).to(self.device)  # (1, T, F)

        out = self.model.predict_proba(x)
        proba = out["direction_proba"][0].cpu().numpy()
        prices = out["prices"][0].cpu().numpy()
        vol = float(out["volatility"][0].cpu())
        conf_head = float(out["confidence"][0].cpu())

        last_close = float(ohlcv["close"].iloc[-1])
        # Confidence blends the classifier's own confidence signal with the
        # softmax margin (how decisive the direction call is).
        margin = float(proba.max() - np.partition(proba, -2)[-2])
        confidence = float(np.clip(0.5 * conf_head + 0.5 * margin, 0.0, 1.0))

        return ModelPrediction(
            p_bullish=float(proba[0]),
            p_bearish=float(proba[1]),
            p_sideways=float(proba[2]),
            predicted_high=last_close * (1.0 + float(prices[0])),
            predicted_low=last_close * (1.0 + float(prices[1])),
            predicted_close=last_close * (1.0 + float(prices[2])),
            expected_volatility=vol,
            confidence=confidence,
        )
