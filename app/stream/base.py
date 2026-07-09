"""Exchange adapter interfaces.

Every supported venue (Binance, Bybit, Coinbase, Forex/Stocks/NSE/MCX brokers)
implements these two Protocols. The rest of the platform depends only on the
interface, so adding a venue never touches the AI or decision layers.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from app.data.schemas import Candle


@runtime_checkable
class MarketDataProvider(Protocol):
    """Historical (REST) market data — used to build the training set."""

    async def fetch_klines(
        self, symbol: str, interval: str, limit: int = 1000, end_time: int | None = None
    ) -> list[Candle]:
        """Return up to ``limit`` closed candles ending at ``end_time`` (ms)."""
        ...


@runtime_checkable
class ExchangeStream(Protocol):
    """Live streaming market data over WebSocket."""

    async def stream_candles(self, symbol: str, interval: str) -> AsyncIterator[Candle]:
        """Yield candles as they update. ``candle.closed`` marks a final bar."""
        ...
