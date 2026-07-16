"""Tests for the purged walk-forward evaluator and the acceptance gate.

These pin down the two things that make the harness trustworthy: it must not leak
future info across the purge gap, and its accept/reject rule must reject noise.
"""

import numpy as np
import pytest

from app.training.challenger_compare import acceptance
from app.training.walk_forward import purged_walk_forward


def _separable(n=3000, seed=0):
    """A dataset where the features genuinely predict the label — the harness should
    score well above chance on it. No look-ahead: the label uses only current X."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 4)).astype(np.float32)
    # label is a clean function of the CURRENT row (no smoothing → no leakage worry)
    s = X[:, 0] + 0.5 * X[:, 1]
    y = np.where(s > 0.4, 0, np.where(s < -0.4, 1, 2)).astype(int)
    return X, y


def test_walk_forward_runs_and_reports_all_metrics():
    X, y = _separable()
    r = purged_walk_forward(X, y, horizon=12, n_folds=5)
    assert r["ok"]
    for key in ("mean_dir_acc", "std_dir_acc", "worst_dir_acc", "mean_balanced_acc",
                "mean_macro_f1", "mean_ece", "mean_coverage", "up_precision"):
        assert key in r and r[key] == r[key]        # present and not NaN


def test_walk_forward_learns_a_real_signal():
    X, y = _separable()
    r = purged_walk_forward(X, y, horizon=12, n_folds=5)
    # feature 0 is genuinely predictive → directional accuracy well above 50%
    assert r["mean_dir_acc"] > 0.6


def test_walk_forward_is_not_suspiciously_high_on_noise():
    rng = np.random.default_rng(1)
    X = rng.normal(size=(3000, 4)).astype(np.float32)
    y = rng.integers(0, 3, size=3000)               # labels independent of X
    r = purged_walk_forward(X, y, horizon=12, n_folds=5)
    # The failure mode we guard against is LEAKAGE inflating accuracy. On pure noise
    # the harness must never report a suspiciously high number (which would signal a
    # leak). Low directional accuracy is fine and expected.
    assert r["mean_dir_acc"] < 0.65


# --- the acceptance gate must reject noise and imbalance ---

def _stats(acc, std=0.026, worst=0.575, bal=0.413, f1=0.405, ece=0.070, upr=0.58, dnr=0.63):
    return {"mean_dir_acc": acc, "std_dir_acc": std, "worst_dir_acc": worst,
            "mean_balanced_acc": bal, "mean_macro_f1": f1, "mean_ece": ece,
            "up_recall": upr, "down_recall": dnr}


def test_tiny_gain_within_noise_is_rejected():
    base = _stats(0.6101)
    chal = _stats(0.6137)                            # +0.36pp, std 2.6pp
    decision, reason = acceptance(base, chal)
    assert decision == "REJECT"
    assert "noise" in reason


def test_gain_from_class_imbalance_is_rejected():
    base = _stats(0.6101, upr=0.58, dnr=0.63)
    # big enough mean gain to clear the noise floor, but it's all one-sided
    chal = _stats(0.64, upr=0.50, dnr=0.75)
    decision, reason = acceptance(base, chal)
    assert decision == "REJECT"
    assert "imbalance" in reason


def test_a_real_balanced_gain_is_accepted():
    base = _stats(0.6101)
    # a large, balanced improvement that clears every gate
    chal = _stats(0.64, worst=0.585, upr=0.61, dnr=0.66)
    decision, _ = acceptance(base, chal)
    assert decision == "ACCEPT"


def test_declining_accuracy_is_rejected():
    base = _stats(0.6101)
    chal = _stats(0.6003)
    assert acceptance(base, chal)[0] == "REJECT"
