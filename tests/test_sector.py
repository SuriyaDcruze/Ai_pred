"""Tests for Sector Intelligence — the sector-rotation context layer.

Offline/deterministic: the pure logic (strength scoring, stock→sector mapping,
support/against) is tested without network. It is context, not an edge — these tests
lock down that it labels and maps correctly.
"""

import numpy as np
import pandas as pd

from app.sector import STOCK_SECTOR, _strength, supports


def _series(per_bar_pct: float, n: int = 120) -> pd.DataFrame:
    """A synthetic index that changes `per_bar_pct`% each bar (monotonic).
    Positive → rising (above its EMA, positive 20-bar return); negative → falling."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    close = 100.0 * np.cumprod(np.full(n, 1 + per_bar_pct / 100))
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=idx)


def test_strength_flags_outperformance():
    strong_sector = _series(0.4)             # rising ~0.4%/bar
    flat_nifty = _series(0.0)                # flat
    s = _strength(strong_sector, flat_nifty)
    assert s is not None
    assert s["rs20"] > 0                      # sector outperformed the index
    assert s["above_ema50"] is True


def test_strength_flags_underperformance():
    weak_sector = _series(-0.4)              # falling
    flat_nifty = _series(0.0)                # flat
    s = _strength(weak_sector, flat_nifty)
    assert s["rs20"] < 0
    assert s["above_ema50"] is False


def test_strength_none_on_thin_data():
    tiny = _series(0.3, n=30)
    assert _strength(tiny, tiny) is None


def test_supports_long_in_strong_sector():
    assert supports(1, {"label": "Strong"}) == "support"
    assert supports(1, {"label": "Weak"}) == "against"
    assert supports(1, {"label": "Neutral"}) == "neutral"


def test_supports_short_in_weak_sector():
    assert supports(-1, {"label": "Weak"}) == "support"
    assert supports(-1, {"label": "Strong"}) == "against"


def test_supports_neutral_when_unknown():
    assert supports(1, None) == "neutral"
    assert supports(1, {"label": "Unknown"}) == "neutral"


def test_bank_stocks_map_to_banking():
    for s in ("HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK"):
        assert STOCK_SECTOR[s] == "Banking"


def test_it_stocks_map_to_it():
    for s in ("TCS", "INFY", "WIPRO"):
        assert STOCK_SECTOR[s] == "IT"
