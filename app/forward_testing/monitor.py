"""Background monitor — periodically resolves open predictions.

A small, self-contained async loop that calls :meth:`ForwardTestingEngine.monitor_once`
on an interval. It is deliberately **not** wired into the FastAPI application here — that
integration (starting it under the app lifespan, injecting the real data provider) belongs
to a later milestone. Keeping it standalone makes it fully unit-testable and keeps this
milestone free of changes to unrelated files.

Restart safety comes from the engine, which re-reads open predictions from the database on
every pass; the monitor holds no prediction state of its own. The blocking
``monitor_once`` runs in a worker thread so it never stalls the event loop.
"""

from __future__ import annotations

import asyncio

import anyio

from app.forward_testing.engine import CandleFetcher, ForwardTestingEngine
from app.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_INTERVAL_SECS = 60.0


class ForwardTestingMonitor:
    """Runs the engine's monitoring pass on a fixed interval."""

    def __init__(
        self,
        engine: ForwardTestingEngine,
        fetch_candles: CandleFetcher,
        interval_secs: float = _DEFAULT_INTERVAL_SECS,
    ):
        """Create the monitor.

        Args:
            engine: The Forward Testing engine to drive.
            fetch_candles: ``(symbol, timeframe) -> list[Candle]`` provider (may block;
                it is run in a worker thread).
            interval_secs: Seconds between monitoring passes.
        """
        self._engine = engine
        self._fetch_candles = fetch_candles
        self._interval = interval_secs
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        """True while the monitor loop is active."""
        return self._task is not None and not self._task.done()

    async def run_pass(self) -> dict[str, int]:
        """Run a single monitoring pass off the event loop; return its summary."""
        return await anyio.to_thread.run_sync(self._engine.monitor_once, self._fetch_candles)

    async def _loop(self) -> None:
        logger.info("forward-testing monitor started (interval %.0fs)", self._interval)
        while True:
            try:
                summary = await self.run_pass()
                if summary["resolved"]:
                    logger.info(
                        "monitor pass: %d checked, %d resolved", summary["checked"], summary["resolved"]
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a bad pass must not kill the loop
                logger.warning("forward-testing monitor pass failed: %s", exc)
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        """Start the monitor loop as a background task (idempotent)."""
        if self.running:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the monitor loop and wait for it to unwind."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            logger.info("forward-testing monitor stopped")
