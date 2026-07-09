"""Binance Spot/Futures market data adapter.

Implements both the historical REST kline fetch (for building training sets) and
the live WebSocket kline stream (for real-time inference). Public market data
needs no API keys. Uses public endpoints; respects the closed-candle flag.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import websockets

from app.data.schemas import Candle
from app.utils.logging import get_logger

logger = get_logger(__name__)

_SPOT_REST = "https://api.binance.com"
_FUT_REST = "https://fapi.binance.com"
_SPOT_WS = "wss://stream.binance.com:9443/ws"
_FUT_WS = "wss://fstream.binance.com/ws"
# Public market-data mirror. Unlike api.binance.com it is NOT geo-blocked, so it
# works from cloud IPs (Colab, CI, datacenters) that get HTTP 451 on the main
# host. Serves the same spot klines with no auth.
_SPOT_DATA_MIRROR = "https://data-api.binance.vision"


class BinanceClient:
    """Historical + live Binance data. ``futures=True`` uses USDⓈ-M futures."""

    def __init__(self, futures: bool = False, timeout: float = 10.0):
        self.futures = futures
        self._rest = _FUT_REST if futures else _SPOT_REST
        self._ws = _FUT_WS if futures else _SPOT_WS
        self._timeout = timeout
        # Hosts to try in order. Spot gets the geo-unblocked mirror as fallback.
        self._rest_hosts = [self._rest]
        if not futures:
            self._rest_hosts.append(_SPOT_DATA_MIRROR)

    @property
    def name(self) -> str:
        return "binance-futures" if self.futures else "binance-spot"

    async def fetch_klines(
        self, symbol: str, interval: str, limit: int = 1000, end_time: int | None = None
    ) -> list[Candle]:
        path = "/fapi/v1/klines" if self.futures else "/api/v3/klines"
        params: dict[str, object] = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time

        last_exc: Exception | None = None
        for host in self._rest_hosts:
            try:
                async with httpx.AsyncClient(base_url=host, timeout=self._timeout) as client:
                    resp = await client.get(path, params=params)
                    resp.raise_for_status()
                    return [self._row_to_candle(r) for r in resp.json()]
            except httpx.HTTPStatusError as exc:
                # 451 (geo-blocked) / 403 (forbidden) -> try the next host.
                if exc.response.status_code in (451, 403) and host is not self._rest_hosts[-1]:
                    logger.warning("Binance host %s returned %s; falling back to mirror.",
                                   host, exc.response.status_code)
                    last_exc = exc
                    continue
                raise
            except httpx.HTTPError as exc:  # network hiccup -> try next host
                last_exc = exc
                continue
        assert last_exc is not None
        raise last_exc

    async def fetch_history(
        self, symbol: str, interval: str, total: int
    ) -> list[Candle]:
        """Page backwards through REST to assemble ``total`` candles."""
        out: list[Candle] = []
        end_time: int | None = None
        while len(out) < total:
            batch = await self.fetch_klines(symbol, interval, limit=min(1000, total - len(out)), end_time=end_time)
            if not batch:
                break
            out = batch + out
            end_time = int(batch[0].open_time.timestamp() * 1000) - 1
            await asyncio.sleep(0.1)  # be gentle with rate limits
        logger.info("Fetched %d %s %s candles from %s", len(out), symbol, interval, self.name)
        return out[-total:]

    async def stream_candles(self, symbol: str, interval: str) -> AsyncIterator[Candle]:
        """Yield live candles. Prefers Binance's WebSocket; if that host is
        geo-blocked (HTTP 451 on cloud IPs) it permanently falls back to polling
        the unblocked REST mirror, so the live feed works everywhere.
        """
        url = f"{self._ws}/{symbol.lower()}@kline_{interval}"
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.info("Connected to %s kline stream %s %s", self.name, symbol, interval)
                    backoff = 1.0
                    async for raw in ws:
                        k = json.loads(raw).get("k")
                        if k:
                            yield self._kline_event_to_candle(k)
            except (websockets.ConnectionClosed, OSError) as exc:  # pragma: no cover - network
                logger.warning("Stream dropped (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception as exc:  # noqa: BLE001 - e.g. geo-block (451) -> poll instead
                logger.warning("Binance WS blocked (%s); switching to REST polling.", exc)
                async for candle in self._poll_candles(symbol, interval):
                    yield candle
                return

    async def _poll_candles(self, symbol: str, interval: str) -> AsyncIterator[Candle]:
        """Poll the REST mirror for the latest candle (~every 4s) as a WS fallback.

        Yields the forming candle (``closed=False``) each poll, and the just-closed
        candle (``closed=True``) once when a new candle begins.
        """
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
            except Exception as exc:  # noqa: BLE001 - network hiccup, keep polling
                logger.warning("poll fetch failed: %s", exc)
            await asyncio.sleep(4.0)

    @staticmethod
    def _row_to_candle(r: list) -> Candle:
        return Candle(
            open_time=datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[4]),
            volume=float(r[5]),
            trades=int(r[8]),
            closed=True,
        )

    @staticmethod
    def _kline_event_to_candle(k: dict) -> Candle:
        return Candle(
            open_time=datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            trades=int(k["n"]),
            closed=bool(k["x"]),
        )
