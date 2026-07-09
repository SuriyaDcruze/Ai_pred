"""Deterministic synthetic OHLCV generator.

Used by tests, the demo script, and offline training so the platform runs with
zero network access. Produces a regime-switching geometric random walk with
volatility clustering — realistic enough to exercise every code path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


def generate_ohlcv(
    n: int = 2000,
    start_price: float = 100.0,
    seed: int = 7,
    interval_minutes: int = 60,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Regime-switching drift: alternate bullish / bearish / ranging blocks.
    drift = np.zeros(n)
    i = 0
    while i < n:
        block = rng.integers(50, 200)
        regime = rng.choice([0.0006, -0.0006, 0.0])
        drift[i : i + block] = regime
        i += block

    vol = 0.01 + 0.5 * np.abs(np.sin(np.linspace(0, 6 * np.pi, n))) * 0.01
    shocks = rng.normal(0, 1, n) * vol + drift
    log_price = np.log(start_price) + np.cumsum(shocks)
    close = np.exp(log_price)

    open_ = np.empty(n)
    open_[0] = start_price
    open_[1:] = close[:-1]
    intrabar = np.abs(rng.normal(0, 1, n)) * vol * close
    high = np.maximum(open_, close) + intrabar
    low = np.minimum(open_, close) - intrabar
    volume = rng.gamma(shape=2.0, scale=500.0, size=n) * (1 + 5 * vol)

    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    index = [start + timedelta(minutes=interval_minutes * k) for k in range(n)]
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "trades": rng.integers(50, 5000, n),
        },
        index=pd.DatetimeIndex(index, name="open_time"),
    )
