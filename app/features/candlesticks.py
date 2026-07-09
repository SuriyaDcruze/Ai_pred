"""Classic candlestick pattern detectors — codified from *The Candlestick Trading
Bible* (and standard TA definitions).

Every pattern is a vectorized, causal detector over OHLC. They return numeric
columns (mostly ±1 / 0, some with a strength magnitude) so the model can learn
from them and the decision engine can gate on them. Patterns are *signals of
location*, not standalone trades — per the book, they matter most at
support/resistance in trend confluence (see ``candlestick_confluence``).

Definitions implemented:
  Single bar : doji, dragonfly doji, gravestone doji, hammer, shooting star,
               bullish/bearish pin bar
  Two bar    : bullish/bearish engulfing, bullish/bearish harami, inside bar,
               tweezer top/bottom
  Three bar  : morning star, evening star
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _anatomy(df: pd.DataFrame) -> dict[str, pd.Series]:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).replace(0.0, np.nan)
    body = (c - o).abs()
    upper = h - c.combine(o, max)
    lower = c.combine(o, min) - l
    return {
        "o": o, "h": h, "l": l, "c": c,
        "rng": rng, "body": body, "upper": upper, "lower": lower,
        "bull": c > o, "bear": c < o,
    }


def add_candlestick_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` augmented with candlestick pattern columns."""
    a = _anatomy(df)
    o, h, l, c = a["o"], a["h"], a["l"], a["c"]
    rng, body, upper, lower = a["rng"], a["body"], a["upper"], a["lower"]
    bull, bear = a["bull"], a["bear"]
    body_ratio = (body / rng).fillna(0.0)

    out = df.copy()

    # --- Doji family: tiny body ---
    is_doji = body_ratio <= 0.1
    out["cdl_doji"] = is_doji.astype(float)
    out["cdl_dragonfly_doji"] = (is_doji & (lower >= 0.6 * rng) & (upper <= 0.1 * rng)).astype(float)
    out["cdl_gravestone_doji"] = (is_doji & (upper >= 0.6 * rng) & (lower <= 0.1 * rng)).astype(float)

    # --- Pin bars / hammer / shooting star (long single wick, small body) ---
    small_body = body_ratio <= 0.34
    bull_pin = small_body & (lower >= 2.0 * body) & (lower >= 0.5 * rng) & (upper <= 0.25 * rng)
    bear_pin = small_body & (upper >= 2.0 * body) & (upper >= 0.5 * rng) & (lower <= 0.25 * rng)
    out["cdl_pin_bull"] = bull_pin.astype(float)   # rejection of lower prices (bullish)
    out["cdl_pin_bear"] = bear_pin.astype(float)   # rejection of higher prices (bearish)
    # Hammer / shooting star are pin bars named by trend context; expose the raw
    # shape here and let the confluence layer add the trend condition.
    out["cdl_hammer"] = bull_pin.astype(float)
    out["cdl_shooting_star"] = bear_pin.astype(float)

    # --- Engulfing (2-bar) ---
    po, pc = o.shift(1), c.shift(1)
    prev_bear = pc < po
    prev_bull = pc > po
    bull_engulf = bull & prev_bear & (c >= po) & (o <= pc)
    bear_engulf = bear & prev_bull & (c <= po) & (o >= pc)
    out["cdl_engulf_bull"] = bull_engulf.astype(float)
    out["cdl_engulf_bear"] = bear_engulf.astype(float)

    # --- Harami (2-bar): small body contained in prior large opposite body ---
    prev_body = (pc - po).abs()
    contained = (c.combine(o, max) <= po.combine(pc, max)) & (c.combine(o, min) >= po.combine(pc, min))
    bull_harami = prev_bear & bull & contained & (body < prev_body)
    bear_harami = prev_bull & bear & contained & (body < prev_body)
    out["cdl_harami_bull"] = bull_harami.astype(float)
    out["cdl_harami_bear"] = bear_harami.astype(float)

    # --- Inside bar (2-bar): fully contained range (mother candle) ---
    ph, pl = h.shift(1), l.shift(1)
    inside = (h <= ph) & (l >= pl)
    out["cdl_inside_bar"] = inside.astype(float)

    # --- Tweezers (2-bar): near-equal highs/lows ---
    tol = 0.0015  # 0.15% of price
    tweezer_top = (np.abs(h - ph) <= tol * h) & prev_bull & bear
    tweezer_bottom = (np.abs(l - pl) <= tol * l) & prev_bear & bull
    out["cdl_tweezer_top"] = tweezer_top.astype(float)
    out["cdl_tweezer_bottom"] = tweezer_bottom.astype(float)

    # --- Stars (3-bar) ---
    o1, c1 = o.shift(2), c.shift(2)   # first candle
    o2, c2 = o.shift(1), c.shift(1)   # middle (small body)
    mid_body = (c2 - o2).abs()
    first_body = (c1 - o1).abs()
    small_mid = mid_body <= 0.5 * first_body.replace(0.0, np.nan)
    # Morning star: big bear, small body, big bull closing into first body
    morning = (c1 < o1) & small_mid & bull & (c > (o1 + c1) / 2.0)
    # Evening star: big bull, small body, big bear closing into first body
    evening = (c1 > o1) & small_mid & bear & (c < (o1 + c1) / 2.0)
    out["cdl_morning_star"] = morning.fillna(False).astype(float)
    out["cdl_evening_star"] = evening.fillna(False).astype(float)

    # --- Aggregate bull/bear pressure score from all reversal patterns ---
    bull_cols = ["cdl_pin_bull", "cdl_engulf_bull", "cdl_harami_bull",
                 "cdl_tweezer_bottom", "cdl_dragonfly_doji", "cdl_morning_star"]
    bear_cols = ["cdl_pin_bear", "cdl_engulf_bear", "cdl_harami_bear",
                 "cdl_tweezer_top", "cdl_gravestone_doji", "cdl_evening_star"]
    out["cdl_bull_score"] = out[bull_cols].sum(axis=1)
    out["cdl_bear_score"] = out[bear_cols].sum(axis=1)
    out["cdl_signal"] = out["cdl_bull_score"] - out["cdl_bear_score"]

    return out


# Columns worth feeding the model (compact, non-redundant set).
CANDLE_FEATURE_COLUMNS: tuple[str, ...] = (
    "cdl_pin_bull", "cdl_pin_bear",
    "cdl_engulf_bull", "cdl_engulf_bear",
    "cdl_harami_bull", "cdl_harami_bear",
    "cdl_inside_bar",
    "cdl_tweezer_top", "cdl_tweezer_bottom",
    "cdl_morning_star", "cdl_evening_star",
    "cdl_doji",
    "cdl_bull_score", "cdl_bear_score", "cdl_signal",
)


# Friendly, beginner-facing descriptions for each pattern (from the book).
# (name, plain-English meaning, direction) keyed by feature column.
PATTERN_INFO: dict[str, tuple[str, str, str]] = {
    "cdl_pin_bull": ("Hammer / Bullish Pin Bar",
                     "long lower wick — buyers rejected lower prices. Often a bounce UP.", "bull"),
    "cdl_pin_bear": ("Shooting Star / Bearish Pin Bar",
                     "long upper wick — sellers rejected higher prices. Often a drop DOWN.", "bear"),
    "cdl_engulf_bull": ("Bullish Engulfing",
                        "a big green candle swallowed the last red one — buyers took control.", "bull"),
    "cdl_engulf_bear": ("Bearish Engulfing",
                        "a big red candle swallowed the last green one — sellers took control.", "bear"),
    "cdl_morning_star": ("Morning Star",
                         "a 3-candle bottom — the trend may be turning UP.", "bull"),
    "cdl_evening_star": ("Evening Star",
                         "a 3-candle top — the trend may be turning DOWN.", "bear"),
    "cdl_harami_bull": ("Bullish Harami",
                        "selling paused inside the last candle — a possible turn UP.", "bull"),
    "cdl_harami_bear": ("Bearish Harami",
                        "buying paused inside the last candle — a possible turn DOWN.", "bear"),
    "cdl_tweezer_bottom": ("Tweezer Bottom",
                           "two matching lows — buyers are defending a floor.", "bull"),
    "cdl_tweezer_top": ("Tweezer Top",
                        "two matching highs — sellers are defending a ceiling.", "bear"),
    "cdl_dragonfly_doji": ("Dragonfly Doji",
                           "long lower wick with tiny body — a possible bounce UP.", "bull"),
    "cdl_gravestone_doji": ("Gravestone Doji",
                            "long upper wick with tiny body — a possible drop DOWN.", "bear"),
    "cdl_inside_bar": ("Inside Bar",
                       "the market is coiling / pausing — a breakout may follow.", "neutral"),
    "cdl_doji": ("Doji",
                 "open and close nearly equal — the market is undecided.", "neutral"),
}


def detected_patterns(row: pd.Series) -> list[dict]:
    """Return the candlestick patterns that fired on this bar, beginner-friendly."""
    found = []
    for col, (name, desc, direction) in PATTERN_INFO.items():
        if float(row.get(col, 0) or 0) > 0:
            found.append({"name": name, "desc": desc, "dir": direction})
    return found


# Beginner lessons so users can *ask* about a pattern by name and learn it.
# Order matters: more specific / multi-word keys and the doji variants come
# first so "dragonfly doji" matches Dragonfly, not the generic Doji.
PATTERN_LESSONS: list[dict] = [
    {"keys": ["shooting star"], "cols": ["cdl_pin_bear"], "title": "Shooting Star ☄️",
     "body": "A small body at the bottom with a long wick sticking UP. Price shot higher but "
             "sellers slammed it back down — buyers failed. After an up-move near a ceiling, it "
             "often warns of a drop DOWN. It's a SELL signal."},
    {"keys": ["morning star"], "cols": ["cdl_morning_star"], "title": "Morning Star 🌅",
     "body": "A 3-candle bottom: a big red candle, a small pause candle, then a big green candle. "
             "It signals a downtrend may be turning UP. A BUY signal."},
    {"keys": ["evening star"], "cols": ["cdl_evening_star"], "title": "Evening Star 🌆",
     "body": "A 3-candle top: a big green candle, a small pause candle, then a big red candle. "
             "It signals an uptrend may be turning DOWN. A SELL signal."},
    {"keys": ["pin bar", "pinbar"], "cols": ["cdl_pin_bull", "cdl_pin_bear"], "title": "Pin Bar 📌",
     "body": "Any candle with a small body and one long wick (a 'pin'). The long wick shows one "
             "side got rejected. Long wick DOWN = bullish (buy); long wick UP = bearish (sell)."},
    {"keys": ["hammer"], "cols": ["cdl_pin_bull"], "title": "Hammer 🔨",
     "body": "A small body up top with a long wick hanging DOWN (like a hammer). Price fell but "
             "buyers pushed it back up — sellers failed. After a down-move near a support floor, "
             "it often means a bounce UP. It's a BUY signal."},
    {"keys": ["dragonfly"], "cols": ["cdl_dragonfly_doji"], "title": "Dragonfly Doji 🐉",
     "body": "A tiny-body candle with a long lower wick and almost no upper wick. Buyers strongly "
             "rejected lower prices — often a bounce UP."},
    {"keys": ["gravestone"], "cols": ["cdl_gravestone_doji"], "title": "Gravestone Doji 🪦",
     "body": "A tiny-body candle with a long upper wick and almost no lower wick. Sellers strongly "
             "rejected higher prices — often a drop DOWN."},
    {"keys": ["engulfing"], "cols": ["cdl_engulf_bull", "cdl_engulf_bear"], "title": "Engulfing 🌯",
     "body": "Two candles where the second fully 'swallows' the first. Big GREEN swallowing a red "
             "= buyers took over (bullish, UP). Big RED swallowing a green = sellers took over "
             "(bearish, DOWN). One of the strongest reversal signals."},
    {"keys": ["harami"], "cols": ["cdl_harami_bull", "cdl_harami_bear"], "title": "Harami 🤰",
     "body": "A small candle sitting INSIDE the previous big candle's body. The strong move lost "
             "steam — a possible pause or reversal. Green-inside-red hints UP; red-inside-green hints DOWN."},
    {"keys": ["inside bar", "inside"], "cols": ["cdl_inside_bar"], "title": "Inside Bar 📦",
     "body": "A candle whose whole range fits inside the previous one. The market is squeezing / "
             "pausing — often right before a breakout. Traders watch which way it breaks out."},
    {"keys": ["tweezer", "tweezers"], "cols": ["cdl_tweezer_top", "cdl_tweezer_bottom"],
     "title": "Tweezers 🔧",
     "body": "Two candles with matching highs (Tweezer Top → SELL) or matching lows (Tweezer "
             "Bottom → BUY). Price hit the same level twice and got rejected — a wall being defended."},
    {"keys": ["doji"], "cols": ["cdl_doji", "cdl_dragonfly_doji", "cdl_gravestone_doji"],
     "title": "Doji ✚",
     "body": "A candle where open and close are almost equal, so it has almost no body. Buyers and "
             "sellers are balanced — indecision. Often warns the current trend may pause or reverse."},
]


def lookup_pattern(text: str) -> dict | None:
    """Find which candlestick pattern (if any) the user is asking about."""
    t = (text or "").lower()
    for lesson in PATTERN_LESSONS:
        if any(k in t for k in lesson["keys"]):
            return lesson
    return None


def candlestick_confluence(features: pd.DataFrame, sr_tolerance: float = 0.004) -> pd.Series:
    """The book's core setup, as a score in [-1, 1].

    A reversal candle earns conviction only in *confluence*: a bullish pattern
    near support with an up-trend structure (or a bearish pattern near
    resistance with a down-trend) scores strongly; the same pattern floating
    mid-range scores ~0. This is the "location + trend" filter the Candlestick
    Trading Bible insists on.
    """
    close = features["close"]
    sup = features.get("donchian_lower")
    res = features.get("donchian_upper")
    trend = features.get("structure_trend")
    ema_fast = features.get("ema_9")
    ema_slow = features.get("ema_21")

    bull_pat = features.get("cdl_bull_score", pd.Series(0.0, index=features.index)) > 0
    bear_pat = features.get("cdl_bear_score", pd.Series(0.0, index=features.index)) > 0

    near_sup = (sup is not None) & (np.abs(close - sup) <= sr_tolerance * close) if sup is not None else False
    near_res = (res is not None) & (np.abs(close - res) <= sr_tolerance * close) if res is not None else False

    up_bias = pd.Series(False, index=features.index)
    dn_bias = pd.Series(False, index=features.index)
    if ema_fast is not None and ema_slow is not None:
        up_bias = up_bias | (ema_fast > ema_slow)
        dn_bias = dn_bias | (ema_fast < ema_slow)
    if trend is not None:
        up_bias = up_bias | (trend > 0)
        dn_bias = dn_bias | (trend < 0)

    score = pd.Series(0.0, index=features.index)
    # Bullish pattern at support, not against a down-trend.
    score = score.mask(bull_pat & near_sup & up_bias, 1.0)
    score = score.mask(bull_pat & near_sup & ~dn_bias, score.where(score != 0, 0.6))
    # Bearish pattern at resistance.
    score = score.mask(bear_pat & near_res & dn_bias, -1.0)
    score = score.mask(bear_pat & near_res & ~up_bias, score.where(score != 0, -0.6))
    return score.fillna(0.0)
