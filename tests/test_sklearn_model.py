"""Tests for the production sklearn predictor and calibration.

These don't require network or a trained checkpoint — they build a tiny model in
memory and check the *contract*: that SklearnPredictor is a genuine drop-in for the
deep Predictor, and that calibration does what it claims.
"""

import numpy as np
import pandas as pd
import pytest

from app.ai.calibration import ProbabilityCalibrator, brier_score, expected_calibration_error
from app.data.schemas import ModelPrediction, Side


# ------------------------------ calibration ------------------------------ #

def test_brier_of_perfect_probabilities_is_zero():
    y = np.array([0, 1, 2, 0])
    perfect = np.eye(3)[y]
    assert brier_score(perfect, y) == pytest.approx(0.0)


def test_brier_of_uninformative_is_two_thirds():
    y = np.array([0, 1, 2] * 10)
    flat = np.full((len(y), 3), 1 / 3)
    assert brier_score(flat, y) == pytest.approx(2 / 3, abs=1e-6)


def test_ece_zero_when_confidence_matches_accuracy():
    # 100% confident and always right -> perfectly calibrated
    conf = np.full(100, 1.0)
    correct = np.ones(100)
    assert expected_calibration_error(conf, correct) == pytest.approx(0.0)


def test_ece_catches_overconfidence():
    # claims 90% sure, actually right 50% of the time -> ECE ~0.40
    conf = np.full(100, 0.9)
    correct = np.array([1, 0] * 50)
    assert expected_calibration_error(conf, correct) == pytest.approx(0.4, abs=0.02)


def test_calibrator_outputs_are_probabilities():
    rng = np.random.default_rng(0)
    raw = rng.dirichlet([1, 1, 1], size=500)
    y = rng.integers(0, 3, size=500)
    cal = ProbabilityCalibrator("isotonic").fit(raw, y)
    out = cal.transform(raw)
    assert out.shape == raw.shape
    assert np.allclose(out.sum(axis=1), 1.0, atol=1e-6)      # rows sum to 1
    assert (out >= 0).all() and (out <= 1).all()


def test_calibrator_survives_a_missing_class():
    # class 2 never appears — must not crash, must pass that column through
    raw = np.random.default_rng(1).dirichlet([1, 1, 1], size=100)
    y = np.random.default_rng(1).integers(0, 2, size=100)   # only 0 and 1
    cal = ProbabilityCalibrator("isotonic").fit(raw, y)
    out = cal.transform(raw)
    assert not np.isnan(out).any()


# --------------------------- predictor contract -------------------------- #

def _toy_predictor():
    """A SklearnPredictor wrapping a trivially-trained logistic model."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    from app.ai.sklearn_model import SklearnPredictor
    from app.features.engineering import FeatureBuilder

    cols = list(FeatureBuilder().feature_columns)
    rng = np.random.default_rng(3)
    X = rng.normal(size=(300, len(cols)))
    y = rng.integers(0, 3, size=300)
    scaler = StandardScaler().fit(X)
    model = LogisticRegression(max_iter=500).fit(scaler.transform(X), y)
    return SklearnPredictor(model, scaler, None, cols, horizon=12, cost_pct=0.0012)


def _synth_ohlcv(n=400):
    rng = np.random.default_rng(5)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    o = close + rng.normal(0, 0.4, n)
    h = np.maximum(o, close) + np.abs(rng.normal(0, 0.5, n))
    low = np.minimum(o, close) - np.abs(rng.normal(0, 0.5, n))
    return pd.DataFrame(
        {"open": o, "high": h, "low": low, "close": close, "volume": rng.uniform(1, 9, n)},
        index=pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
    )


def test_predictor_returns_a_valid_model_prediction():
    pred = _toy_predictor().predict(_synth_ohlcv())
    assert isinstance(pred, ModelPrediction)
    # the three class probabilities must sum to ~1
    assert pred.p_bullish + pred.p_bearish + pred.p_sideways == pytest.approx(1.0, abs=1e-6)
    assert 0.0 <= pred.confidence <= 1.0
    assert pred.direction in (Side.BUY, Side.SELL, Side.WAIT)


def test_predictor_confidence_is_the_called_class_probability():
    pred = _toy_predictor().predict(_synth_ohlcv())
    assert pred.confidence == pytest.approx(
        max(pred.p_bullish, pred.p_bearish, pred.p_sideways)
    )


def test_predictor_rejects_short_history():
    with pytest.raises(ValueError):
        _toy_predictor().predict(_synth_ohlcv(n=50))


def test_predicted_prices_bracket_the_last_close():
    df = _synth_ohlcv()
    pred = _toy_predictor().predict(df)
    last = df["close"].iloc[-1]
    assert pred.predicted_low <= last <= pred.predicted_high
