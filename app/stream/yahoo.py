"""Yahoo Finance market-data adapter — brings **stocks** to the platform.

Binance only has crypto. This adapter uses Yahoo's public chart endpoint (no API
key, no account) so the same model, dashboard and tracker work on Apple (AAPL),
ITC (ITC.NS), Tesla, Reliance, indices — anything Yahoo lists.

Implements the same :class:`MarketDataProvider` / :class:`ExchangeStream` shape as
:class:`BinanceClient`, so nothing downstream changes.

Honest limits (they matter):
  * Data is **delayed** (typically ~15 min) — it is not a real-time feed.
  * There is **no WebSocket**; "live" updates are REST polling.
  * Stocks only trade during **market hours** — outside them the candle stops moving.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from app.data.schemas import Candle
from app.utils.logging import get_logger

logger = get_logger(__name__)

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; AegisBot/1.0)"}

# Our timeframe -> Yahoo interval. Yahoo's intraday history is limited, so we
# also pick the largest range it will serve for that interval.
_INTERVAL: dict[str, tuple[str, str]] = {
    "1m": ("1m", "7d"),
    "2m": ("2m", "60d"),
    "5m": ("5m", "60d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "1h": ("60m", "730d"),
    "1d": ("1d", "10y"),
    "1wk": ("1wk", "10y"),
}


class YahooClient:
    """Historical + polled 'live' data for stocks, ETFs and indices."""

    def __init__(self, timeout: float = 12.0):
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "yahoo"

    @staticmethod
    def is_stock(symbol: str) -> bool:
        """Crypto pairs on this platform end in USDT; anything else is a stock."""
        return not symbol.upper().endswith(("USDT", "BUSD", "USDC"))

    async def fetch_klines(
        self, symbol: str, interval: str, limit: int = 1000, end_time: int | None = None
    ) -> list[Candle]:
        yf_interval, yf_range = _INTERVAL.get(interval, ("1d", "5y"))
        url = _CHART.format(symbol=symbol.upper())
        async with httpx.AsyncClient(timeout=self._timeout, headers=_UA) as client:
            resp = await client.get(url, params={"interval": yf_interval, "range": yf_range})
            resp.raise_for_status()
            data = resp.json()

        result = (data.get("chart") or {}).get("result") or []
        if not result:
            raise ValueError(f"Yahoo returned no data for {symbol!r} ({interval})")
        r = result[0]
        stamps = r.get("timestamp") or []
        q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
        opens, highs, lows, closes, vols = (
            q.get("open") or [], q.get("high") or [], q.get("low") or [],
            q.get("close") or [], q.get("volume") or [],
        )

        candles: list[Candle] = []
        for i, ts in enumerate(stamps):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            if None in (o, h, l, c):      # Yahoo pads gaps (holidays, halts) with nulls
                continue
            candles.append(
                Candle(
                    open_time=datetime.fromtimestamp(ts, tz=timezone.utc),
                    open=float(o), high=float(h), low=float(l), close=float(c),
                    volume=float(vols[i] or 0), trades=0, closed=True,
                )
            )
        return candles[-limit:]

    async def fetch_history(self, symbol: str, interval: str, total: int) -> list[Candle]:
        candles = await self.fetch_klines(symbol, interval, limit=total)
        logger.info("Fetched %d %s %s candles from yahoo", len(candles), symbol.upper(), interval)
        return candles

    async def stream_candles(
        self, symbol: str, interval: str, poll_secs: float = 5.0
    ) -> AsyncIterator[Candle]:
        """'Live' feed via polling — Yahoo has no WebSocket, and data is delayed."""
        last_open = None
        while True:
            try:
                rows = await self.fetch_klines(symbol, interval, limit=2)
                if rows:
                    forming = rows[-1]
                    if last_open is not None and forming.open_time != last_open and len(rows) >= 2:
                        prev = rows[-2]
                        prev.closed = True
                        yield prev
                    last_open = forming.open_time
                    forming.closed = False
                    yield forming
            except Exception as exc:  # noqa: BLE001 - keep polling through hiccups
                logger.warning("yahoo poll failed for %s: %s", symbol, exc)
                await asyncio.sleep(10.0)
                continue
            await asyncio.sleep(poll_secs)
