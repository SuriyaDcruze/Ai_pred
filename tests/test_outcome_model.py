"""Tests for the target-before-stop outcome model.

The two things that make it trustworthy: path-dependent labels that resolve the
same-candle tie conservatively, and out-of-fold direction probabilities that never
leak in-sample confidence.
"""

import numpy as np

from app.ai.outcome_model import (
    NO_MOVE,
    STOP_FIRST,
    TARGET_FIRST,
    build_outcome_features,
    direction_side,
    oof_direction_probs,
    outcome_labels,
)


def test_long_target_first():
    high = np.array([100, 101, 103.5, 104, 105.0])
    low = np.array([99, 100, 101, 102, 103.0])
    close = np.array([100, 100.5, 103, 104, 105.0])
    side = np.array([1, 0, 0, 0, 0])
    atr = np.array([2.0, 2, 2, 2, 2])          # entry 100 → tp 103, sl 98
    lab, r = outcome_labels(high, low, close, side, atr, horizon=4)
    assert lab[0] == TARGET_FIRST and r[0] == 1.5


def test_long_stop_first():
    high = np.array([100, 99, 98, 97, 96.0])
    low = np.array([99, 97.5, 96, 95, 94.0])
    close = np.array([100, 98, 97, 96, 95.0])
    side = np.array([1, 0, 0, 0, 0])
    atr = np.array([2.0, 2, 2, 2, 2])          # sl 98 breached on bar 1
    lab, r = outcome_labels(high, low, close, side, atr, horizon=4)
    assert lab[0] == STOP_FIRST and r[0] == -1.0


def test_short_target_first():
    # short entry 100, tp = 97, sl = 102; price falls → target first
    high = np.array([100, 100.5, 99, 98, 97.0])
    low = np.array([99, 98, 96.5, 96, 95.0])
    close = np.array([100, 99, 98, 97, 96.0])
    side = np.array([-1, 0, 0, 0, 0])
    atr = np.array([2.0, 2, 2, 2, 2])
    lab, r = outcome_labels(high, low, close, side, atr, horizon=4)
    assert lab[0] == TARGET_FIRST and r[0] == 1.5


def test_same_candle_tie_is_pessimistic():
    # a single candle that spans BOTH tp and sl → must be labelled STOP_FIRST
    high = np.array([100, 104.0, 104, 104, 104])
    low = np.array([99, 97.0, 98, 98, 98])      # bar 1 low 97 <= sl 98 AND high 104 >= tp 103
    close = np.array([100, 100, 100, 100, 100.0])
    side = np.array([1, 0, 0, 0, 0])
    atr = np.array([2.0, 2, 2, 2, 2])
    lab, r = outcome_labels(high, low, close, side, atr, horizon=4)
    assert lab[0] == STOP_FIRST and r[0] == -1.0


def test_no_trade_side_is_unlabelled():
    close = np.full(6, 100.0)
    lab, r = outcome_labels(close, close, close, np.zeros(6), np.full(6, 2.0), horizon=3)
    assert (lab == -1).all()


def test_direction_side_mapping():
    probs = np.array([[0.7, 0.2, 0.1], [0.2, 0.7, 0.1], [0.2, 0.1, 0.7]])
    assert list(direction_side(probs)) == [1, -1, 0]     # UP→long, DOWN→short, NEUTRAL→none


def test_oof_probs_are_not_in_sample():
    """The first fold's rows must keep the neutral prior — they were never trained on."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 5)).astype(np.float32)
    y = rng.integers(0, 3, size=2000)
    probs = oof_direction_probs(X, y, horizon=12, n_folds=5)
    assert probs.shape == (2000, 3)
    # earliest rows stay at the 1/3 prior (no model has seen enough history yet)
    assert np.allclose(probs[0], 1 / 3)
    # rows always sum to ~1
    assert np.allclose(probs.sum(axis=1), 1.0, atol=1e-6)


def test_outcome_features_extend_the_base():
    base = np.zeros((10, 45), dtype=np.float32)
    probs = np.full((10, 3), 1 / 3)
    out = build_outcome_features(base, probs)
    assert out.shape == (10, 45 + 5)             # +p_up,p_dn,p_side,entropy,margin
    assert np.isfinite(out).all()
