"""Tests for the confidence-bucket analyzer.

The analyzer's whole job is to stop us fooling ourselves, so these tests pin down
the two ways it could lie: mislabelling a thin bucket as trustworthy, and computing
expectancy that ignores cost.
"""

import numpy as np

from app.evaluation.confidence_analysis import BUCKETS, MIN_BUCKET_SAMPLES, _net_expectancy


def test_buckets_are_contiguous_and_cover_50_to_100():
    # every bucket's top equals the next bucket's bottom, starting at 0.50
    assert BUCKETS[0][0] == 0.50
    for (lo, hi), (nlo, nhi) in zip(BUCKETS, BUCKETS[1:]):
        assert hi == nlo
    assert BUCKETS[-1][1] > 1.0            # top bucket includes 1.00


def test_perfect_direction_is_positive_expectancy():
    pred = np.array([0, 1, 0, 1])
    truth = np.array([0, 1, 0, 1])         # all correct
    reach = np.full(4, 0.02)
    exp, calls = _net_expectancy(pred, truth, reach, cost_pct=0.0012)
    assert calls == 4
    assert exp > 0                         # winning 1.5R each, minus tiny cost


def test_all_wrong_is_negative_expectancy():
    pred = np.array([0, 0, 0, 0])
    truth = np.array([1, 1, 1, 1])         # all wrong
    reach = np.full(4, 0.02)
    exp, _ = _net_expectancy(pred, truth, reach, cost_pct=0.0012)
    assert exp < 0


def test_neutral_predictions_are_not_counted_as_trades():
    pred = np.array([2, 2, 0, 1])          # first two are NEUTRAL -> no trade
    truth = np.array([0, 1, 0, 1])
    reach = np.full(4, 0.02)
    _, calls = _net_expectancy(pred, truth, reach, cost_pct=0.0012)
    assert calls == 2


def test_cost_reduces_expectancy():
    pred = np.array([0, 1, 0, 1])
    truth = np.array([0, 1, 0, 1])
    reach = np.full(4, 0.02)
    cheap, _ = _net_expectancy(pred, truth, reach, cost_pct=0.0001)
    dear, _ = _net_expectancy(pred, truth, reach, cost_pct=0.0100)
    assert dear < cheap                    # higher cost -> lower expectancy


def test_min_bucket_threshold_is_conservative():
    # a bucket needs a real sample before we believe it
    assert MIN_BUCKET_SAMPLES >= 30
