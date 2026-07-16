"""Sector Intelligence — understand the sector before the stock (V3 mandate #2).

Indian equities move by **sector rotation**: when Banking is weak, a bank-stock long
is a lower-quality setup regardless of the stock's own chart. This engine ranks the
major NSE sectors by strength (relative to the Nifty 50) and momentum, maps each stock
to its sector, and exposes that as **honest context** — never as a standalone signal
or a model feature (we proved features don't add trade-selection edge; this is for
explainability and decision quality, exactly as the mandate requires).

Data: NSE sector indices via Yahoo (^NSEBANK, ^CNXIT, …), cached briefly so the
screener/intelligence endpoints don't refetch per call.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from app.utils.logging import get_logger

logger = get_logger(__name__)

# NSE sector indices on Yahoo Finance.
SECTOR_INDICES: dict[str, str] = {
    "Banking": "^NSEBANK",
    "IT": "^CNXIT",
    "Auto": "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "FMCG": "^CNXFMCG",
    "Energy": "^CNXENERGY",
    "Metal": "^CNXMETAL",
    "Realty": "^CNXREALTY",
    "Infra": "^CNXINFRA",
    "PSU Bank": "^CNXPSUBANK",
}

# Map liquid NSE stocks to their sector (extend as the basket grows).
STOCK_SECTOR: dict[str, str] = {
    "RELIANCE": "Energy", "ONGC": "Energy", "NTPC": "Energy", "POWERGRID": "Energy",
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
    "AXISBANK": "Banking", "KOTAKBANK": "Banking", "INDUSINDBK": "Banking",
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto", "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto",
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma", "DIVISLAB": "Pharma",
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "TATASTEEL": "Metal", "JSWSTEEL": "Metal", "HINDALCO": "Metal", "COALINDIA": "Metal",
    "LT": "Infra", "ADANIPORTS": "Infra", "ULTRACEMCO": "Infra",
    "BHARTIARTL": "Infra", "BAJFINANCE": "Banking", "TITAN": "FMCG", "ASIANPAINT": "FMCG",
}

_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_TTL = 900   # 15 min


def _fetch(symbol: str, interval: str, total: int = 400) -> pd.DataFrame | None:
    import asyncio

    key = f"{symbol}:{interval}"
    now = time.time()
    if key in _CACHE and now - _CACHE[key][0] < _TTL:
        return _CACHE[key][1]
    try:
        from app.data.schemas import candles_to_frame
        from app.stream.yahoo import YahooClient

        df = candles_to_frame(asyncio.run(YahooClient().fetch_history(symbol, interval, total=total)))
        _CACHE[key] = (now, df)
        return df
    except Exception as exc:  # noqa: BLE001
        logger.warning("sector fetch %s failed: %s", symbol, exc)
        return None


def _strength(idx: pd.DataFrame, nifty: pd.DataFrame) -> dict | None:
    """Relative strength + momentum of one sector index vs the Nifty. Past-only."""
    if idx is None or nifty is None or len(idx) < 60:
        return None
    c = idx["close"]
    nclose = nifty["close"].reindex(idx.index, method="ffill")
    # relative strength: sector 20d return minus Nifty 20d return
    rs20 = float((c.pct_change(20).iloc[-1] - nclose.pct_change(20).iloc[-1]) * 100)
    rs50 = float((c.pct_change(50).iloc[-1] - nclose.pct_change(50).iloc[-1]) * 100)
    # momentum: is the sector index above its own 50-EMA and rising?
    ema50 = c.ewm(span=50, adjust=False).mean()
    above = bool(c.iloc[-1] > ema50.iloc[-1])
    slope = float((ema50.iloc[-1] - ema50.iloc[-6]) / ema50.iloc[-6] * 100) if len(ema50) > 6 else 0.0
    return {"rs20": round(rs20, 2), "rs50": round(rs50, 2),
            "above_ema50": above, "ema_slope_pct": round(slope, 2)}


def sector_rankings(interval: str = "1d") -> dict:
    """Rank all sectors by relative strength vs Nifty. Cached."""
    nifty = _fetch("^NSEI", interval)
    if nifty is None:
        return {"available": False}
    rows = []
    for name, sym in SECTOR_INDICES.items():
        s = _strength(_fetch(sym, interval), nifty)
        if s:
            # a simple strength score: 20d relative strength + a momentum bonus
            score = s["rs20"] + (2.0 if s["above_ema50"] else -2.0) + s["ema_slope_pct"]
            rows.append({"sector": name, "score": round(score, 2), **s})
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["label"] = ("Strong" if r["score"] > 3 else "Weak" if r["score"] < -3 else "Neutral")
    return {"available": True, "sectors": rows, "n": len(rows)}


def sector_for_stock(symbol: str, interval: str = "1d") -> dict | None:
    """The sector context for one stock: its sector's strength, momentum, and rank."""
    plain = symbol.upper().replace(".NS", "")
    sector = STOCK_SECTOR.get(plain)
    if sector is None:
        return None
    ranks = sector_rankings(interval)
    if not ranks.get("available"):
        return {"sector": sector, "label": "Unknown", "note": "sector index unavailable"}
    for r in ranks["sectors"]:
        if r["sector"] == sector:
            return {"sector": sector, "label": r["label"], "rank": r["rank"],
                    "of": ranks["n"], "rs20": r["rs20"], "score": r["score"],
                    "above_ema50": r["above_ema50"]}
    return {"sector": sector, "label": "Unknown"}


def supports(side: int, sector_ctx: dict | None) -> str:
    """Does the sector support a long/short? Returns 'support' | 'against' | 'neutral'."""
    if not sector_ctx or sector_ctx.get("label") in (None, "Unknown"):
        return "neutral"
    strong = sector_ctx["label"] == "Strong"
    weak = sector_ctx["label"] == "Weak"
    if side > 0:
        return "support" if strong else "against" if weak else "neutral"
    if side < 0:
        return "support" if weak else "against" if strong else "neutral"
    return "neutral"
