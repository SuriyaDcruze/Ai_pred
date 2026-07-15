"""Textbook-example tests for the extended candlestick library.

A pattern detector that never fires is indistinguishable from a pattern that is
merely rare — which is exactly how a silent bug hides for months. So every
detector here is fed a *hand-built, textbook* instance of its pattern and must
recognise it. If one of these fails, the detector is wrong, not the market.
"""

import pandas as pd
import pytest

from app.features.patterns_extra import EXTENDED_PATTERN_INFO, add_extended_patterns


def _df(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """bars = [(open, high, low, close), ...] — oldest first."""
    return pd.DataFrame(
        {
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [1000.0] * len(bars),
        },
        index=pd.date_range("2024-01-01", periods=len(bars), freq="1h", tz="UTC"),
    )


def _fires(col: str, bars: list[tuple[float, float, float, float]]) -> bool:
    """Did ``col`` fire on the LAST bar of ``bars``?"""
    out = add_extended_patterns(_df(bars))
    return bool(out[col].iloc[-1] > 0)


# Padding so trend/rolling context (5-bar lookback) is well defined.
FLAT = [(100, 100.5, 99.5, 100)] * 6
RISE = [(100 + i, 101 + i, 99 + i, 100.8 + i) for i in range(6)]      # uptrend
FALL = [(120 - i, 121 - i, 119 - i, 119.2 - i) for i in range(6)]     # downtrend


# ------------------------------- single bar ------------------------------ #

def test_bullish_marubozu():
    assert _fires("cdl_marubozu_bull", FLAT + [(100, 110.1, 99.9, 110)])


def test_bearish_marubozu():
    assert _fires("cdl_marubozu_bear", FLAT + [(110, 110.1, 99.9, 100)])


def test_spinning_top():
    # body must be small but NOT doji-small, with real wicks on both sides
    assert _fires("cdl_spinning_top", FLAT + [(100, 106, 94, 102.5)])


def test_long_legged_doji():
    assert _fires("cdl_long_legged_doji", FLAT + [(100, 110, 90, 100.2)])


def test_four_price_doji():
    assert _fires("cdl_four_price_doji", FLAT + [(100, 100, 100, 100)])


def test_bullish_belt_hold():
    # opens exactly at the low, closes near the high
    assert _fires("cdl_belt_hold_bull", FLAT + [(100, 108, 100, 107)])


def test_bearish_belt_hold():
    assert _fires("cdl_belt_hold_bear", FLAT + [(108, 108, 100, 101)])


def test_hanging_man_needs_an_uptrend():
    """Same shape: a Hammer at a bottom, a Hanging Man at a top. Context decides."""
    hammer_shape = (106, 106.4, 100, 106.2)
    assert _fires("cdl_hanging_man", RISE + [hammer_shape])       # at a top -> hanging man
    assert not _fires("cdl_hanging_man", FALL + [(114, 114.4, 108, 114.2)])  # in a fall -> not


def test_inverted_hammer_needs_a_downtrend():
    assert _fires("cdl_inverted_hammer", FALL + [(114, 120, 113.6, 114.2)])
    assert not _fires("cdl_inverted_hammer", RISE + [(106, 112, 105.6, 106.2)])


# -------------------------------- two bar -------------------------------- #

def test_piercing_line():
    # long red, then a green that opens BELOW its low and closes past the midpoint
    assert _fires("cdl_piercing", FLAT + [(110, 110.5, 99.5, 100), (98, 106.5, 97.5, 106)])


def test_dark_cloud_cover():
    assert _fires("cdl_dark_cloud", FLAT + [(100, 110.5, 99.5, 110), (112, 112.5, 103, 103.5)])


def test_bullish_kicker():
    # red candle, then a full gap up — no overlap at all
    assert _fires("cdl_kicker_bull", FLAT + [(110, 110.5, 104.5, 105), (111, 118, 110.8, 117)])


def test_bearish_kicker():
    assert _fires("cdl_kicker_bear", FLAT + [(105, 110.5, 104.5, 110), (104, 104.2, 96, 97)])


def test_outside_bar():
    assert _fires("cdl_outside_bar", FLAT + [(100, 102, 98, 101), (99, 105, 95, 104)])


def test_gaps():
    assert _fires("cdl_gap_up", FLAT + [(100, 102, 98, 101), (104, 106, 103, 105)])
    assert _fires("cdl_gap_down", FLAT + [(100, 102, 98, 101), (96, 97, 94, 95)])


def test_matching_low():
    assert _fires("cdl_matching_low", FLAT + [(110, 110.5, 99.5, 100), (104, 104.5, 99.8, 100)])


def test_matching_high():
    assert _fires("cdl_matching_high", FLAT + [(100, 110.5, 99.5, 110), (106, 110.2, 105.5, 110)])


def test_bullish_counterattack():
    # long red, then gap down but closes right back at the prior close
    assert _fires("cdl_counterattack_bull", FLAT + [(110, 110.5, 99.5, 100), (95, 100.2, 94.5, 100)])


def test_bearish_counterattack():
    assert _fires("cdl_counterattack_bear", FLAT + [(100, 110.5, 99.5, 110), (115, 115.5, 109.8, 110)])


def test_homing_pigeon():
    # a harami, but both candles red — selling losing steam
    assert _fires("cdl_homing_pigeon", FLAT + [(110, 110.5, 99.5, 100), (108, 108.5, 101.5, 102)])


def test_on_neck_line():
    # in a downtrend: a bounce that dies exactly at the prior low
    # the bounce must close *at* the prior low (109.5), not merely near it
    assert _fires("cdl_on_neck", FALL + [(120, 120.5, 109.5, 110), (108, 110.1, 107.5, 109.6)])


def test_thrusting_line():
    # a bounce into the prior body, but failing below its midpoint
    assert _fires("cdl_thrusting", FALL + [(120, 120.5, 109.5, 110), (108, 114, 107.5, 113)])


def test_separating_lines():
    assert _fires("cdl_separating_bull", FLAT + [(110, 110.5, 99.5, 100), (110, 118.2, 109.8, 118)])
    assert _fires("cdl_separating_bear", FLAT + [(100, 110.5, 99.5, 110), (100, 100.2, 91.8, 92)])


# ------------------------------- three bar ------------------------------- #

def test_three_white_soldiers():
    bars = FLAT + [
        (100, 106.2, 99.8, 106),      # long green
        (102, 110.2, 101.8, 110),     # opens inside prior body, closes higher
        (106, 114.2, 105.8, 114),
    ]
    assert _fires("cdl_three_white_soldiers", bars)


def test_three_black_crows():
    bars = FLAT + [
        (114, 114.2, 105.8, 106),
        (110, 110.2, 101.8, 102),
        (106, 106.2, 97.8, 98),
    ]
    assert _fires("cdl_three_black_crows", bars)


def test_identical_three_crows():
    # each opens exactly at the previous close
    bars = FLAT + [
        (114, 114.2, 105.8, 106),
        (106, 106.2, 101.8, 102),
        (102, 102.2, 97.8, 98),
    ]
    assert _fires("cdl_identical_three_crows", bars)


def test_three_inside_up():
    bars = FLAT + [
        (110, 110.5, 99.5, 100),      # big red
        (102, 108.5, 101.5, 108),     # bullish harami inside it
        (108, 114, 107.5, 113),       # confirms: closes above the first candle's open
    ]
    assert _fires("cdl_three_inside_up", bars)


def test_three_inside_down():
    bars = FLAT + [
        (100, 110.5, 99.5, 110),
        (108, 108.5, 101.5, 102),
        (102, 102.5, 96, 97),
    ]
    assert _fires("cdl_three_inside_down", bars)


def test_three_outside_up():
    bars = FLAT + [
        (105, 105.5, 101.5, 102),     # red
        (101, 108.5, 100.5, 108),     # bullish engulfing
        (108, 112, 107.5, 111),       # confirmation
    ]
    assert _fires("cdl_three_outside_up", bars)


def test_three_outside_down():
    bars = FLAT + [
        (102, 105.5, 101.5, 105),
        (106, 106.5, 100.5, 101),
        (101, 101.5, 96, 97),
    ]
    assert _fires("cdl_three_outside_down", bars)


def test_abandoned_baby_bullish():
    # a doji marooned by a gap on BOTH sides
    bars = FLAT + [
        (110, 110.5, 100, 100.5),     # big red
        (97, 97.6, 96.4, 97.05),      # doji, gapped fully below (high < prior low)
        (99, 106, 98.5, 105),         # gapped fully above the doji (low > doji high)
    ]
    assert _fires("cdl_abandoned_baby_bull", bars)


def test_abandoned_baby_bearish():
    bars = FLAT + [
        (100, 110, 99.5, 109.5),
        (113, 113.6, 112.4, 113.05),
        (111, 111.5, 104, 105),
    ]
    assert _fires("cdl_abandoned_baby_bear", bars)


def test_morning_doji_star():
    bars = FLAT + [
        (110, 110.5, 99.5, 100),      # big red
        (99, 100.2, 98.0, 99.05),     # doji
        (100, 108, 99.5, 107),        # closes above the first body's midpoint (105)
    ]
    assert _fires("cdl_morning_doji_star", bars)


def test_evening_doji_star():
    bars = FLAT + [
        (100, 110.5, 99.5, 110),
        (111, 112.0, 109.8, 110.95),
        (110, 110.5, 102, 103),
    ]
    assert _fires("cdl_evening_doji_star", bars)


def test_tri_star():
    doji = lambda p: (p, p + 1.0, p - 1.0, p + 0.05)  # noqa: E731
    assert _fires("cdl_tri_star_bull", FALL + [doji(114), doji(113.5), doji(113)])
    assert _fires("cdl_tri_star_bear", RISE + [doji(106), doji(106.5), doji(107)])


def test_advance_block():
    # three greens, each weaker, each with a longer upper wick — the rally tiring
    bars = FLAT + [
        (100, 106.5, 99.8, 106),      # body 6.0, upper 0.5
        (106, 112.0, 105.8, 110),     # body 4.0, upper 2.0
        (110, 116.0, 109.8, 112),     # body 2.0, upper 4.0
    ]
    assert _fires("cdl_advance_block", bars)


def test_deliberation():
    bars = FLAT + [
        (100, 106.2, 99.8, 106),      # long green
        (106, 112.2, 105.8, 112),     # long green
        (112, 114.0, 111.8, 112.5),   # small body stalling at the top
    ]
    assert _fires("cdl_deliberation", bars)


def test_stick_sandwich():
    bars = FLAT + [
        (105, 105.5, 99.5, 100),      # red closing at 100
        (99, 103.5, 98.5, 103),       # green between
        (104, 104.5, 99.5, 100),      # red closing at 100 again — a floor
    ]
    assert _fires("cdl_stick_sandwich", bars)


def test_tasuki_gaps():
    up = FLAT + [
        (100, 104.2, 99.8, 104),      # green
        (106, 110.2, 105.8, 110),     # gaps up (low 105.8 > prior high 104.2)
        (109, 109.5, 105.0, 105.5),   # opens in body, closes in the gap, doesn't fill it
    ]
    assert _fires("cdl_upside_tasuki_gap", up)

    down = FLAT + [
        (110, 110.2, 105.8, 106),     # red
        (104, 104.2, 99.8, 100),      # gaps down
        (101, 104.5, 100.5, 104.4),   # closes back into the gap, doesn't fill it
    ]
    assert _fires("cdl_downside_tasuki_gap", down)


def test_unique_three_river_bottom():
    bars = FLAT + [
        (110, 110.5, 99.5, 100),      # long red
        (101, 101.2, 94.0, 100.5),    # digs to a NEW low but closes above the prior close
        (98, 98.5, 97.5, 98.2),       # small candle below it
    ]
    assert _fires("cdl_unique_three_river", bars)


# -------------------------------- five bar ------------------------------- #

def test_rising_three_methods():
    bars = FLAT + [
        (100, 110.2, 99.8, 110),      # big green
        (108, 108.5, 105.5, 106),     # three small candles resting INSIDE its range
        (107, 107.5, 104.5, 105),
        (106, 106.5, 103.5, 104),
        (105, 114.2, 104.8, 114),     # big green closing above the first
    ]
    assert _fires("cdl_rising_three_methods", bars)


def test_falling_three_methods():
    bars = FLAT + [
        (110, 110.2, 99.8, 100),      # big red
        (102, 104.5, 101.5, 104),     # rest inside its range
        (103, 105.5, 102.5, 105),
        (104, 106.5, 103.5, 106),
        (105, 105.2, 95.8, 96),       # big red closing below the first
    ]
    assert _fires("cdl_falling_three_methods", bars)


# ------------------------------ the invariant ---------------------------- #

def test_every_declared_pattern_has_a_detector():
    """No pattern may be advertised in the catalogue without a column behind it."""
    out = add_extended_patterns(_df(FLAT))
    missing = [c for c in EXTENDED_PATTERN_INFO if c not in out.columns]
    assert not missing, f"advertised but never computed: {missing}"


def test_model_feature_width_is_untouched():
    """The trained checkpoint expects exactly 45 features. These are display-only.

    If this test fails, artifacts/model_best.pt will refuse to load.
    """
    from app.features.engineering import FeatureBuilder

    assert len(FeatureBuilder().feature_columns) == 45


@pytest.mark.parametrize("col", list(EXTENDED_PATTERN_INFO))
def test_detectors_never_produce_nan(col):
    out = add_extended_patterns(_df(FLAT + RISE + FALL))
    assert out[col].notna().all(), f"{col} produced NaN"
    assert out[col].isin([0.0, 1.0]).all(), f"{col} is not a clean 0/1 flag"
