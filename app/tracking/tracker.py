"""Forward-testing call tracker — the honest 'does it actually work?' engine.

Every time the user taps the chart (or a live signal fires), we save the exact
point they marked: time, price, and the BUY/SELL call with its stop and targets.
Later, as real candles print, :func:`resolve_call` replays the *future* price
against that call and scores it WIN / LOSS / OPEN — the same no-lookahead,
stop-first rule the backtester uses. Aggregated, these calls give a real,
forward-tested win rate on live data.

Storage is a simple JSON file (``data/calls.json``) — no database needed.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field

from app.data.schemas import Candle

_LOCK = threading.Lock()
_DEFAULT_PATH = os.path.join("data", "calls.json")


@dataclass
class TrackedCall:
    id: str
    created_at: str          # ISO time the call was made
    symbol: str
    timeframe: str
    side: str                # BUY / SELL
    entry: float
    stop: float
    tp1: float
    tp2: float
    clicked_time: int        # epoch seconds of the candle the user tapped
    clicked_price: float
    source: str = "manual"   # "manual" (you tapped) | "ai" (AI's own pick)
    # Filled in on evaluation:
    status: str = "OPEN"     # WIN / LOSS / OPEN
    resolved_time: int | None = None
    resolved_price: float | None = None
    r_multiple: float = 0.0  # +reward multiple on win, -1 on loss, 0 open


def resolve_call(call: TrackedCall, candles: list[Candle]) -> TrackedCall:
    """Score a call against candles that occurred AFTER it was made.

    Walks forward bar by bar. If a single bar spans both the stop and the
    target, we pessimistically assume the stop hit first (we can't see intrabar
    order). Leaves the call OPEN if neither level is reached yet.
    """
    long = call.side == "BUY"
    risk = abs(call.entry - call.stop) or 1e-9
    for c in candles:
        t = int(c.open_time.timestamp())
        if t <= call.clicked_time:
            continue
        hit_stop = c.low <= call.stop if long else c.high >= call.stop
        hit_tp = c.high >= call.tp1 if long else c.low <= call.tp1
        if hit_stop:  # pessimistic: stop wins ties
            call.status, call.resolved_time, call.resolved_price = "LOSS", t, call.stop
            call.r_multiple = -1.0
            return call
        if hit_tp:
            call.status, call.resolved_time, call.resolved_price = "WIN", t, call.tp1
            call.r_multiple = round(abs(call.tp1 - call.entry) / risk, 2)
            return call
    call.status, call.resolved_time, call.resolved_price, call.r_multiple = "OPEN", None, None, 0.0
    return call


class CallStore:
    """Thread-safe JSON-file store of tracked calls."""

    def __init__(self, path: str = _DEFAULT_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _read(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _write(self, rows: list[dict]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        os.replace(tmp, self.path)

    def add(self, call: TrackedCall) -> TrackedCall:
        with _LOCK:
            rows = self._read()
            rows.append(asdict(call))
            self._write(rows)
        return call

    def all(self) -> list[TrackedCall]:
        return [TrackedCall(**r) for r in self._read()]

    def save_all(self, calls: list[TrackedCall]) -> None:
        with _LOCK:
            self._write([asdict(c) for c in calls])

    def clear(self) -> None:
        with _LOCK:
            self._write([])

    def remove_last(self, n: int) -> int:
        """Delete the ``n`` most recently added calls. Returns how many were removed."""
        with _LOCK:
            rows = self._read()
            n = min(n, len(rows))
            self._write(rows[:-n] if n else rows)
            return n


def _stats(calls: list[TrackedCall]) -> dict:
    wins = sum(1 for c in calls if c.status == "WIN")
    losses = sum(1 for c in calls if c.status == "LOSS")
    open_ = sum(1 for c in calls if c.status == "OPEN")
    decided = wins + losses
    avg_r = round(sum(c.r_multiple for c in calls if c.status in ("WIN", "LOSS")) / decided, 3) if decided else 0.0
    return {
        "total": len(calls),
        "wins": wins,
        "losses": losses,
        "open": open_,
        "win_rate_pct": round(100 * wins / decided, 1) if decided else None,
        "expectancy_r": avg_r,
    }


def summarize(calls: list[TrackedCall]) -> dict:
    """Overall stats plus a separate breakdown for the AI's picks vs your taps."""
    out = _stats(calls)
    out["ai"] = _stats([c for c in calls if c.source == "ai"])
    out["manual"] = _stats([c for c in calls if c.source != "ai"])
    return out
