"""Future-invariance (leakage) tests for the new feature groups.

The single property that separates a real feature from a leaky one:

    Changing a FUTURE candle must NOT change any feature value at an earlier bar.

If it does, the feature is peeking ahead, and any accuracy it produces is a lie.
This is the exact test the Accuracy Improvement spec mandates, applied to every new
feature group before it's allowed anywhere near the model.
"""

import numpy as np
import pandas as pd
import pytest

from app.features.market_regime import REGIME_FEATURES, add_market_regime
from app.features.price_action import PRICE_ACTION_FEATURES, add_price_action
from app.features.session import SESSION_FEATURES, add_session_features
from app.indicators.technical import add_all_indicators


def _ohlcv(n=400, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    o = close + rng.normal(0, 0.4, n)
    h = np.maximum(o, close) + np.abs(rng.normal(0, 0.6, n))
    low = np.minimum(o, close) - np.abs(rng.normal(0, 0.6, n))
    return pd.DataFrame(
        {"open": o, "high": h, "low": low, "close": close, "volume": rng.uniform(1, 9, n)},
        index=pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
    )


def _assert_future_invariant(builder, cols, is_stock=False):
    """Core check: mutate candles AFTER a cut, verify earlier feature rows unchanged."""
    df = _ohlcv()
    cut = 300                                    # timestamp index we protect

    def build(frame):
        base = add_all_indicators(frame)         # regime/PA need indicators present
        if builder is add_session_features:
            return builder(base, is_stock=is_stock)
        return builder(base)

    original = build(df)

    tampered = df.copy()
    # corrupt EVERYTHING after the cut — prices, highs, lows, closes, volume
    rng = np.random.default_rng(99)
    sl = slice(cut + 1, None)
    for c in ("open", "high", "low", "close"):
        tampered.iloc[cut + 1 :, tampered.columns.get_loc(c)] *= (1 + rng.normal(0, 0.1, len(df) - cut - 1))
    tampered.iloc[cut + 1 :, tampered.columns.get_loc("volume")] *= 3.0
    # keep OHLC self-consistent after tampering
    tampered["high"] = tampered[["open", "high", "low", "close"]].max(axis=1)
    tampered["low"] = tampered[["open", "high", "low", "close"]].min(axis=1)

    after = build(tampered)

    a = original[list(cols)].iloc[: cut + 1].to_numpy()
    b = after[list(cols)].iloc[: cut + 1].to_numpy()
    # feature rows at and before the cut must be identical
    bad = ~np.isclose(np.nan_to_num(a), np.nan_to_num(b), atol=1e-8)
    if bad.any():
        first = np.argwhere(bad)[0]
        col = list(cols)[first[1]]
        raise AssertionError(f"LEAKAGE: '{col}' at row {first[0]} changed when future candles changed")


def test_market_regime_is_future_invariant():
    _assert_future_invariant(add_market_regime, REGIME_FEATURES)


def test_price_action_is_future_invariant():
    _assert_future_invariant(add_price_action, PRICE_ACTION_FEATURES)


def test_session_features_are_future_invariant():
    _assert_future_invariant(add_session_features, SESSION_FEATURES, is_stock=False)


def test_session_stock_variant_is_future_invariant():
    _assert_future_invariant(add_session_features, SESSION_FEATURES, is_stock=True)


# --- sanity: features actually get produced, and are finite ---

@pytest.mark.parametrize("builder,cols,kw", [
    (add_market_regime, REGIME_FEATURES, {}),
    (add_price_action, PRICE_ACTION_FEATURES, {}),
])
def test_features_are_finite(builder, cols, kw):
    df = add_all_indicators(_ohlcv())
    out = builder(df, **kw)
    for c in cols:
        assert c in out.columns
        assert np.isfinite(out[c].to_numpy()).all(), f"{c} has non-finite values"


def test_session_cyclical_encoding_wraps():
    df = add_all_indicators(_ohlcv())
    out = add_session_features(df, is_stock=False)
    # sin/cos must stay in [-1, 1]
    for c in ("ses_hour_sin", "ses_hour_cos", "ses_dow_sin", "ses_dow_cos"):
        v = out[c].to_numpy()
        assert v.min() >= -1.0001 and v.max() <= 1.0001


def test_no_shift_minus_one_in_source():
    """Guardrail: the forbidden leakage idioms must not appear in the feature code."""
    import pathlib

    import ast

    for name in ("market_regime.py", "price_action.py", "session.py"):
        src = pathlib.Path("app/features") / name
        tree = ast.parse(src.read_text(encoding="utf-8"))
        # strip docstrings — we only care about executable code, not comments
        code = "\n".join(
            ln for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for ln in [ast.unparse(node)]
        )
        assert "shift(-1)" not in code, f"{name} uses forbidden shift(-1)"
        assert "center=True" not in code, f"{name} uses forbidden centered rolling"
