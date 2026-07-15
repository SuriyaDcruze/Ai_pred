"""The **full** candlestick pattern library — everything beyond the core 14.

`candlesticks.py` holds the compact set the *model* is trained on. That set is
deliberately small: 15 feature columns, chosen because they fire often enough to
carry signal and don't duplicate each other. **Do not add to it casually** — the
trained model in ``artifacts/model_best.pt`` expects exactly 45 input features and
will refuse to load if that number changes.

This module is the other half: every remaining classic pattern, detected for
*display and education* — chart markers, the patterns panel, the chat assistant.
The model does not see these. That is a deliberate split, not an oversight.

A word of honesty about what you're getting
-------------------------------------------
More patterns is not more accuracy, and anyone who tells you otherwise is selling
something. Three facts worth holding onto:

* **Most of these are rare.** "Concealing Baby Swallow" may fire a handful of times
  in years of data. You cannot build an edge on a sample size of four. Each pattern
  below carries an honest ``rarity`` tag — ``common``, ``uncommon``, or ``rare``.
* **They overlap.** A Dragonfly Doji, a Hammer and a Bullish Pin Bar are largely the
  same event — a long lower wick — described three ways. Feeding all three to a model
  adds noise, not information.
* **Bulkowski measured them.** Across decades of data, the *best* patterns land around
  55-60%; the average sits near a coin flip. None is 90%.

Their real value here is **explanation**: they let the platform show you *why* the
market did what it did, and teach you to read a chart. Use them for that.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# rarity → roughly how often you'll actually see it on a chart
COMMON, UNCOMMON, RARE = "common", "uncommon", "rare"


def _tol(series: pd.Series, pct: float = 0.0015) -> pd.Series:
    """Absolute tolerance for 'near-equal' prices (default 0.15%)."""
    return series.abs() * pct


def add_extended_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Augment ``df`` with every non-core candlestick pattern.

    Display-only: none of these columns are in ``CANDLE_FEATURE_COLUMNS``, so the
    model's input width is unchanged and the trained checkpoint still loads.
    """
    out = df.copy()
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]

    rng = (h - l).replace(0.0, np.nan)
    body = (c - o).abs()
    top = c.combine(o, max)          # top of the body
    bot = c.combine(o, min)          # bottom of the body
    upper = h - top
    lower = bot - l
    bull, bear = c > o, c < o
    body_ratio = (body / rng).fillna(0.0)

    long_body = body_ratio >= 0.6
    small_body = body_ratio <= 0.3
    is_doji = body_ratio <= 0.1

    # previous bars
    o1, h1, l1, c1 = o.shift(1), h.shift(1), l.shift(1), c.shift(1)
    o2, h2, l2, c2 = o.shift(2), h.shift(2), l.shift(2), c.shift(2)
    o3, c3 = o.shift(3), c.shift(3)
    o4, c4 = o.shift(4), c.shift(4)

    bull1, bear1 = c1 > o1, c1 < o1
    bull2, bear2 = c2 > o2, c2 < o2
    body1, body2 = (c1 - o1).abs(), (c2 - o2).abs()
    rng1 = (h1 - l1).replace(0.0, np.nan)
    long_body1 = (body1 / rng1).fillna(0.0) >= 0.6
    mid1 = (o1 + c1) / 2.0                     # midpoint of the previous body

    # context: is the market already trending into this bar?
    downtrend = c1 < c.shift(5)
    uptrend = c1 > c.shift(5)

    def flag(cond) -> pd.Series:
        return cond.fillna(False).astype(float)

    # ----------------------------- single bar ----------------------------- #
    out["cdl_marubozu_bull"] = flag(bull & (body_ratio >= 0.9))
    out["cdl_marubozu_bear"] = flag(bear & (body_ratio >= 0.9))
    out["cdl_spinning_top"] = flag(small_body & ~is_doji & (upper >= 0.25 * rng) & (lower >= 0.25 * rng))
    out["cdl_long_legged_doji"] = flag(is_doji & (upper >= 0.3 * rng) & (lower >= 0.3 * rng))
    # NB: use the raw range, not `rng` — `rng` maps 0 to NaN to make division safe,
    # and a Four Price Doji is *precisely* the zero-range case.
    out["cdl_four_price_doji"] = flag((h - l) <= _tol(c, 0.0005))
    out["cdl_high_wave"] = flag(small_body & ((upper + lower) >= 0.85 * rng) & (rng >= 1.5 * rng.rolling(10).mean()))
    out["cdl_belt_hold_bull"] = flag(bull & long_body & (lower <= 0.03 * rng))
    out["cdl_belt_hold_bear"] = flag(bear & long_body & (upper <= 0.03 * rng))

    # Shape alone doesn't name these — context does. A long-lower-wick candle is a
    # Hammer at a bottom but a Hanging Man at a top. Same shape, opposite meaning.
    long_lower = small_body & (lower >= 2.0 * body) & (upper <= 0.25 * rng)
    long_upper = small_body & (upper >= 2.0 * body) & (lower <= 0.25 * rng)
    out["cdl_hanging_man"] = flag(long_lower & uptrend)       # bearish: at a top
    out["cdl_inverted_hammer"] = flag(long_upper & downtrend)  # bullish: at a bottom

    # ------------------------------ two bar ------------------------------- #
    # Piercing Line: bear, then a bull that opens below its low and closes back
    # past the midpoint of its body — but not all the way through it.
    out["cdl_piercing"] = flag(bear1 & long_body1 & bull & (o < l1) & (c > mid1) & (c < o1))
    # Dark Cloud Cover: the mirror image.
    out["cdl_dark_cloud"] = flag(bull1 & long_body1 & bear & (o > h1) & (c < mid1) & (c > o1))

    # Kicker: a gap straight through, no overlap at all. Rare and violent.
    out["cdl_kicker_bull"] = flag(bear1 & bull & (o >= o1) & (l > h1))
    out["cdl_kicker_bear"] = flag(bull1 & bear & (o <= o1) & (h < l1))

    out["cdl_outside_bar"] = flag((h > h1) & (l < l1))
    out["cdl_gap_up"] = flag(l > h1)
    out["cdl_gap_down"] = flag(h < l1)

    out["cdl_matching_low"] = flag(bear1 & bear & (np.abs(c - c1) <= _tol(c)))
    out["cdl_matching_high"] = flag(bull1 & bull & (np.abs(c - c1) <= _tol(c)))

    # Counterattack: opens with a gap, then closes right back at the prior close.
    out["cdl_counterattack_bull"] = flag(bear1 & long_body1 & bull & (o < l1) & (np.abs(c - c1) <= _tol(c)))
    out["cdl_counterattack_bear"] = flag(bull1 & long_body1 & bear & (o > h1) & (np.abs(c - c1) <= _tol(c)))

    # Homing Pigeon: a harami, but both candles the same colour.
    out["cdl_homing_pigeon"] = flag(bear1 & long_body1 & bear & (o <= o1) & (c >= c1) & (body < body1))

    # Bear continuation family — a weak bounce that fails inside the prior bear body.
    out["cdl_on_neck"] = flag(downtrend & bear1 & long_body1 & bull & (o < l1) & (np.abs(c - l1) <= _tol(c)))
    out["cdl_thrusting"] = flag(downtrend & bear1 & long_body1 & bull & (o < l1) & (c > c1) & (c < mid1))

    out["cdl_separating_bull"] = flag(bear1 & bull & long_body & (np.abs(o - o1) <= _tol(o)))
    out["cdl_separating_bear"] = flag(bull1 & bear & long_body & (np.abs(o - o1) <= _tol(o)))

    # ----------------------------- three bar ------------------------------ #
    # Three White Soldiers: three strong bulls, each closing higher, each opening
    # inside the previous body (no gaps — that would be exhaustion, not strength).
    long_body2 = (body2 / (h2 - l2).replace(0.0, np.nan)).fillna(0.0) >= 0.6
    # The open must land *within* the previous body — inclusive at the close, because
    # opening exactly at the prior close is the Identical Three Crows variant, which
    # is a stricter member of this family, not an exception to it.
    tws = (
        bull2 & bull1 & bull & long_body2 & long_body1 & long_body
        & (c1 > c2) & (c > c1)
        & (o1 >= o2) & (o1 <= c2) & (o >= o1) & (o <= c1)
    )
    tbc = (
        bear2 & bear1 & bear & long_body2 & long_body1 & long_body
        & (c1 < c2) & (c < c1)
        & (o1 <= o2) & (o1 >= c2) & (o <= o1) & (o >= c1)
    )
    out["cdl_three_white_soldiers"] = flag(tws)
    out["cdl_three_black_crows"] = flag(tbc)
    # Identical Three Crows: the same, but each opens right at the prior close.
    out["cdl_identical_three_crows"] = flag(tbc & (np.abs(o1 - c2) <= _tol(o1)) & (np.abs(o - c1) <= _tol(o)))

    # Three Inside Up/Down: a harami, then confirmation.
    harami_bull_1 = bear2 & long_body2 & bull1 & (o1 >= c2) & (c1 <= o2)
    harami_bear_1 = bull2 & long_body2 & bear1 & (o1 <= c2) & (c1 >= o2)
    out["cdl_three_inside_up"] = flag(harami_bull_1 & bull & (c > o2))
    out["cdl_three_inside_down"] = flag(harami_bear_1 & bear & (c < o2))

    # Three Outside Up/Down: an engulfing, then confirmation.
    engulf_bull_1 = bear2 & bull1 & (c1 >= o2) & (o1 <= c2)
    engulf_bear_1 = bull2 & bear1 & (c1 <= o2) & (o1 >= c2)
    out["cdl_three_outside_up"] = flag(engulf_bull_1 & bull & (c > c1))
    out["cdl_three_outside_down"] = flag(engulf_bear_1 & bear & (c < c1))

    # Abandoned Baby: a doji completely gapped away on BOTH sides. Textbook, and
    # genuinely rare — it needs two clean gaps around an indecision candle.
    doji1 = ((body1 / rng1).fillna(1.0) <= 0.1)
    out["cdl_abandoned_baby_bull"] = flag(bear2 & doji1 & (h1 < l2) & bull & (l > h1))
    out["cdl_abandoned_baby_bear"] = flag(bull2 & doji1 & (l1 > h2) & bear & (h < l1))

    # Morning/Evening Star where the middle candle is specifically a doji — the
    # strongest version of the pattern, because indecision is unambiguous.
    small_mid = body1 <= 0.5 * body2
    out["cdl_morning_doji_star"] = flag(bear2 & long_body2 & doji1 & bull & (c > (o2 + c2) / 2.0))
    out["cdl_evening_doji_star"] = flag(bull2 & long_body2 & doji1 & bear & (c < (o2 + c2) / 2.0))

    # Tri-Star: three dojis in a row. Total paralysis.
    doji2 = ((body2 / (h2 - l2).replace(0.0, np.nan)).fillna(1.0) <= 0.1)
    tri = doji2 & doji1 & is_doji
    out["cdl_tri_star_bull"] = flag(tri & downtrend)
    out["cdl_tri_star_bear"] = flag(tri & uptrend)

    # Advance Block: three bulls, but each one weaker than the last, with growing
    # upper wicks. The rally is running out of breath.
    upper1 = h1 - c1.combine(o1, max)
    upper2 = h2 - c2.combine(o2, max)
    out["cdl_advance_block"] = flag(
        bull2 & bull1 & bull & (c > c1) & (c1 > c2)
        & (body1 < body2) & (body < body1)
        & (upper > upper1) & (upper1 > upper2)
    )
    # Deliberation: two strong bulls, then a small one that stalls at the top.
    out["cdl_deliberation"] = flag(bull2 & bull1 & bull & long_body2 & long_body1 & small_body & (c > c1) & (c1 > c2))

    # Stick Sandwich: two bears with a bull between them, closing at the same price.
    out["cdl_stick_sandwich"] = flag(bear2 & bull1 & bear & (np.abs(c - c2) <= _tol(c)) & (o > c1))

    # Tasuki Gap — a gap, then a partial fill that fails. Continuation, not reversal.
    out["cdl_upside_tasuki_gap"] = flag(bull2 & bull1 & (l1 > h2) & bear & (o > o1) & (o < c1) & (c < o1) & (c > c2))
    out["cdl_downside_tasuki_gap"] = flag(bear2 & bear1 & (h1 < l2) & bull & (o < o1) & (o > c1) & (c > o1) & (c < c2))

    # Unique Three River Bottom: rare bullish bottom.
    out["cdl_unique_three_river"] = flag(
        bear2 & long_body2 & bear1 & (l1 < l2) & (c1 > c2) & (o1 <= o2)
        & bull & small_body & (c < c1)
    )

    # ------------------------------ five bar ------------------------------ #
    # Rising/Falling Three Methods: a long candle, a three-candle pause that stays
    # inside its range, then a long candle in the ORIGINAL direction. The classic
    # continuation pattern — the trend rested, it didn't turn.
    o_4, c_4, h_4, l_4 = o.shift(4), c.shift(4), h.shift(4), l.shift(4)
    body_4 = (c_4 - o_4).abs()
    rng_4 = (h_4 - l_4).replace(0.0, np.nan)
    long_4 = (body_4 / rng_4).fillna(0.0) >= 0.6

    inside_3 = (
        (h.shift(3) <= h_4) & (l.shift(3) >= l_4)
        & (h2 <= h_4) & (l2 >= l_4)
        & (h1 <= h_4) & (l1 >= l_4)
    )
    out["cdl_rising_three_methods"] = flag(
        (c_4 > o_4) & long_4 & inside_3 & bull & long_body & (c > c_4)
    )
    out["cdl_falling_three_methods"] = flag(
        (c_4 < o_4) & long_4 & inside_3 & bear & long_body & (c < c_4)
    )

    return out


# (name, plain-English meaning, direction, rarity) keyed by column.
EXTENDED_PATTERN_INFO: dict[str, tuple[str, str, str, str]] = {
    # --- single bar ---
    "cdl_marubozu_bull": ("Bullish Marubozu", "a solid green candle with almost no wicks — buyers ran the whole session.", "bull", COMMON),
    "cdl_marubozu_bear": ("Bearish Marubozu", "a solid red candle with almost no wicks — sellers ran the whole session.", "bear", COMMON),
    "cdl_spinning_top": ("Spinning Top", "small body, wicks both sides — a tug-of-war with no winner.", "neutral", COMMON),
    "cdl_long_legged_doji": ("Long-Legged Doji", "huge wicks both ways, no body — violent indecision.", "neutral", UNCOMMON),
    "cdl_four_price_doji": ("Four Price Doji", "open, high, low and close all the same — dead market.", "neutral", RARE),
    "cdl_high_wave": ("High Wave", "an unusually big candle with a tiny body — panic and confusion.", "neutral", UNCOMMON),
    "cdl_belt_hold_bull": ("Bullish Belt Hold", "opens at the low and never looks back — buyers seized it from the bell.", "bull", COMMON),
    "cdl_belt_hold_bear": ("Bearish Belt Hold", "opens at the high and falls all session — sellers seized it from the bell.", "bear", COMMON),
    "cdl_hanging_man": ("Hanging Man", "a hammer shape, but at the TOP of an up-move — the same candle, the opposite warning. Bearish.", "bear", UNCOMMON),
    "cdl_inverted_hammer": ("Inverted Hammer", "a shooting-star shape, but at the BOTTOM of a fall — buyers testing higher. Bullish.", "bull", UNCOMMON),
    # --- two bar ---
    "cdl_piercing": ("Piercing Line", "a red candle, then a green one that opens lower and claws back past its midpoint. Bullish.", "bull", UNCOMMON),
    "cdl_dark_cloud": ("Dark Cloud Cover", "a green candle, then a red one that opens higher and closes back below its midpoint. Bearish.", "bear", UNCOMMON),
    "cdl_kicker_bull": ("Bullish Kicker", "a red candle, then a gap straight up with no overlap at all. Violent change of mind.", "bull", RARE),
    "cdl_kicker_bear": ("Bearish Kicker", "a green candle, then a gap straight down with no overlap. The strongest bearish signal there is.", "bear", RARE),
    "cdl_outside_bar": ("Outside Bar", "this candle's range swallows the last one entirely — volatility expanding.", "neutral", COMMON),
    "cdl_gap_up": ("Gap Up", "price jumped and left a hole. Gaps often act as magnets and get filled later.", "bull", COMMON),
    "cdl_gap_down": ("Gap Down", "price dropped and left a hole. Gaps often get filled later.", "bear", COMMON),
    "cdl_matching_low": ("Matching Low", "two red candles closing at the same price — a floor is being tested.", "bull", UNCOMMON),
    "cdl_matching_high": ("Matching High", "two green candles closing at the same price — a ceiling is being tested.", "bear", UNCOMMON),
    "cdl_counterattack_bull": ("Bullish Counterattack", "gaps down, then fights all the way back to the last close. Sellers rebuffed.", "bull", RARE),
    "cdl_counterattack_bear": ("Bearish Counterattack", "gaps up, then sinks all the way back to the last close. Buyers rebuffed.", "bear", RARE),
    "cdl_homing_pigeon": ("Homing Pigeon", "a small red candle nesting inside a big red one — the selling is losing steam.", "bull", UNCOMMON),
    "cdl_on_neck": ("On-Neck Line", "a weak bounce that dies right at the last low. The downtrend is still in charge.", "bear", RARE),
    "cdl_thrusting": ("Thrusting Line", "a bounce that fails below the midpoint. Not enough to turn it.", "bear", UNCOMMON),
    "cdl_separating_bull": ("Bullish Separating Lines", "opens exactly where the last candle opened, then runs up. Uptrend resuming.", "bull", RARE),
    "cdl_separating_bear": ("Bearish Separating Lines", "opens exactly where the last candle opened, then runs down. Downtrend resuming.", "bear", RARE),
    # --- three bar ---
    "cdl_three_white_soldiers": ("Three White Soldiers", "three strong green candles marching up, each opening inside the last. Powerful and bullish.", "bull", UNCOMMON),
    "cdl_three_black_crows": ("Three Black Crows", "three strong red candles marching down. Powerful and bearish.", "bear", UNCOMMON),
    "cdl_identical_three_crows": ("Identical Three Crows", "three black crows, each opening exactly at the last close. Relentless selling.", "bear", RARE),
    "cdl_three_inside_up": ("Three Inside Up", "a bullish harami, then a candle that confirms it. The turn is real.", "bull", UNCOMMON),
    "cdl_three_inside_down": ("Three Inside Down", "a bearish harami, then a candle that confirms it.", "bear", UNCOMMON),
    "cdl_three_outside_up": ("Three Outside Up", "a bullish engulfing, then confirmation. Stronger than the engulfing alone.", "bull", COMMON),
    "cdl_three_outside_down": ("Three Outside Down", "a bearish engulfing, then confirmation.", "bear", COMMON),
    "cdl_abandoned_baby_bull": ("Abandoned Baby (Bullish)", "a doji marooned by gaps on BOTH sides at a bottom. Textbook — and genuinely rare.", "bull", RARE),
    "cdl_abandoned_baby_bear": ("Abandoned Baby (Bearish)", "a doji marooned by gaps on both sides at a top.", "bear", RARE),
    "cdl_morning_doji_star": ("Morning Doji Star", "a morning star whose middle candle is a doji — the strongest version.", "bull", UNCOMMON),
    "cdl_evening_doji_star": ("Evening Doji Star", "an evening star whose middle candle is a doji — the strongest version.", "bear", UNCOMMON),
    "cdl_tri_star_bull": ("Tri-Star (Bullish)", "three dojis in a row after a fall. Total paralysis, often before a turn up.", "bull", RARE),
    "cdl_tri_star_bear": ("Tri-Star (Bearish)", "three dojis in a row after a rally.", "bear", RARE),
    "cdl_advance_block": ("Advance Block", "three green candles, but each weaker with longer upper wicks. The rally is running out of breath.", "bear", UNCOMMON),
    "cdl_deliberation": ("Deliberation", "two strong green candles, then one that stalls at the top. Hesitation.", "bear", UNCOMMON),
    "cdl_stick_sandwich": ("Stick Sandwich", "two red candles with a green one between, closing at the same price. A floor.", "bull", RARE),
    "cdl_upside_tasuki_gap": ("Upside Tasuki Gap", "a gap up, then a partial fill that fails. The uptrend continues.", "bull", RARE),
    "cdl_downside_tasuki_gap": ("Downside Tasuki Gap", "a gap down, then a partial fill that fails. The downtrend continues.", "bear", RARE),
    "cdl_unique_three_river": ("Unique Three River Bottom", "a rare bottoming pattern — a new low that gets rejected, then a quiet candle.", "bull", RARE),
    # --- five bar ---
    "cdl_rising_three_methods": ("Rising Three Methods", "a big green candle, a three-candle rest inside it, then another big green. The trend rested — it didn't turn.", "bull", RARE),
    "cdl_falling_three_methods": ("Falling Three Methods", "a big red candle, a quiet pause inside it, then another big red. The downtrend continues.", "bear", RARE),
}

EXTENDED_COLUMNS: tuple[str, ...] = tuple(EXTENDED_PATTERN_INFO)


def extended_detected(row: pd.Series) -> list[dict]:
    """Which of the extended patterns fired on this bar."""
    found = []
    for col, (name, desc, direction, rarity) in EXTENDED_PATTERN_INFO.items():
        if float(row.get(col, 0) or 0) > 0:
            found.append({"name": name, "desc": desc, "dir": direction, "rarity": rarity})
    return found
