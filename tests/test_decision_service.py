"""Tests for the decision engine, model forward pass, and end-to-end service."""

import torch

from app.ai.dataset import SequenceDataset, make_labels
from app.ai.model import HybridTradingModel, ModelConfig
from app.data.schemas import ModelPrediction, Side
from app.decision.engine import DecisionEngine
from app.features.engineering import FeatureBuilder
from app.service import AnalysisService
from app.utils.format import format_signal


def test_model_forward_shapes():
    cfg = ModelConfig(n_features=29, seq_len=32, tcn_channels=(16, 16), d_model=32, n_heads=4, n_transformer_layers=1)
    model = HybridTradingModel(cfg)
    x = torch.randn(4, 32, 29)
    out = model(x)
    assert out["direction_logits"].shape == (4, 3)
    assert out["prices"].shape == (4, 3)
    assert out["volatility"].shape == (4,)
    assert (out["confidence"] >= 0).all() and (out["confidence"] <= 1).all()


def test_model_predict_proba_sums_to_one():
    cfg = ModelConfig(n_features=10, seq_len=16, tcn_channels=(8,), d_model=16, n_heads=2, n_transformer_layers=1)
    model = HybridTradingModel(cfg)
    out = model.predict_proba(torch.randn(2, 16, 10))
    assert torch.allclose(out["direction_proba"].sum(-1), torch.ones(2), atol=1e-5)


def test_labels_are_causal(ohlcv):
    c = ohlcv["close"].to_numpy()
    h = ohlcv["high"].to_numpy()
    lo = ohlcv["low"].to_numpy()
    labels = make_labels(c, h, lo, horizon=5)
    # last `horizon` labels have no future -> direction == -1
    assert (labels["direction"][-5:] == -1).all()
    assert set(labels["direction"][labels["direction"] >= 0].tolist()).issubset({0, 1, 2})


def test_dataset_windows(ohlcv):
    fb = FeatureBuilder()
    feats = fb.fit_transform(ohlcv)
    labels = make_labels(ohlcv["close"].to_numpy(), ohlcv["high"].to_numpy(), ohlcv["low"].to_numpy(), horizon=5)
    ds = SequenceDataset(feats, labels, seq_len=32, horizon=5)
    assert len(ds) > 0
    window, target = ds[0]
    assert window.shape == (32, fb.n_features)
    assert target["prices"].shape == (3,)


def test_decision_wait_on_sideways(ohlcv):
    fb = FeatureBuilder()
    frame = fb.build_frame(ohlcv)
    pred = ModelPrediction(
        p_bullish=0.2, p_bearish=0.2, p_sideways=0.6,
        predicted_high=1, predicted_low=1, predicted_close=1,
        expected_volatility=0.01, confidence=0.9,
    )
    decision = DecisionEngine().evaluate(frame, pred)
    assert decision.side is Side.WAIT


def test_decision_gate_blocks_low_confidence(ohlcv):
    fb = FeatureBuilder()
    frame = fb.build_frame(ohlcv)
    pred = ModelPrediction(
        p_bullish=0.7, p_bearish=0.15, p_sideways=0.15,
        predicted_high=1, predicted_low=1, predicted_close=1,
        expected_volatility=0.01, confidence=0.5,  # below 0.80 gate
    )
    decision = DecisionEngine(min_confidence=0.80).evaluate(frame, pred)
    assert decision.side is Side.WAIT


def test_service_end_to_end_produces_signal(ohlcv):
    service = AnalysisService()  # heuristic mode
    sig = service.analyze(ohlcv, "TESTUSDT", "synthetic", "1h")
    assert sig.decision in (Side.BUY, Side.SELL, Side.WAIT)
    assert 0.0 <= sig.confidence <= 1.0
    text = format_signal(sig)
    assert "Market Status" in text and "Final Recommendation" in text
    # Heuristic mode must never fire a live trade (confidence capped < 0.80).
    assert sig.decision is Side.WAIT


def test_service_rejects_short_history():
    import pandas as pd
    from app.data.synthetic import generate_ohlcv

    service = AnalysisService()
    short = generate_ohlcv(n=30)
    try:
        service.analyze(short, "X", "y", "1h")
        assert False, "should have raised"
    except ValueError:
        pass
