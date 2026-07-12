"""Forward-testing call tracker — the honest 'does it actually work?' engine.

Every AI pick (and every call you tap) is saved: the exact point, the BUY/SELL
call, its stop and targets. Later, as real candles print, :func:`resolve_call`
replays the *future* price against that call and scores it WIN / LOSS / OPEN —
the same no-lookahead, stop-first rule the backtester uses.

Storage is **SQLite** (``data/calls.db``): atomic writes, no half-written files,
safe under concurrent access — unlike the JSON file this replaces.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import asdict, dataclass, fields

from app.data.schemas import Candle

_LOCK = threading.Lock()
_DEFAULT_PATH = os.path.join("data", "calls.db")


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
    clicked_time: int        # epoch seconds of the candle this call is pinned to
    clicked_price: float
    source: str = "manual"   # "manual" (you tapped) | "ai" (AI's own pick)
    # Filled in on evaluation:
    status: str = "OPEN"     # WIN / LOSS / OPEN
    resolved_time: int | None = None
    resolved_price: float | None = None
    r_multiple: float = 0.0


def resolve_call(call: TrackedCall, candles: list[Candle]) -> TrackedCall:
    """Score a call against candles that occurred AFTER it was made.

    Walks forward bar by bar. If a single bar spans both the stop and the target,
    we pessimistically assume the stop hit first (we can't see intrabar order).
    Leaves the call OPEN if neither level is reached yet.
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


_COLUMNS = [f.name for f in fields(TrackedCall)]


class CallStore:
    """SQLite-backed store of tracked calls."""

    def __init__(self, path: str = _DEFAULT_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with _LOCK, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    id             TEXT PRIMARY KEY,
                    created_at     TEXT NOT NULL,
                    symbol         TEXT NOT NULL,
                    timeframe      TEXT NOT NULL,
                    side           TEXT NOT NULL,
                    entry          REAL NOT NULL,
                    stop           REAL NOT NULL,
                    tp1            REAL NOT NULL,
                    tp2            REAL NOT NULL,
                    clicked_time   INTEGER NOT NULL,
                    clicked_price  REAL NOT NULL,
                    source         TEXT NOT NULL DEFAULT 'manual',
                    status         TEXT NOT NULL DEFAULT 'OPEN',
                    resolved_time  INTEGER,
                    resolved_price REAL,
                    r_multiple     REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_created ON calls(created_at DESC)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_calls_market ON calls(symbol, timeframe, source, clicked_time)"
            )
            # One AI pick per candle per market — enforced by the DB, not by hand.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_once_per_candle "
                "ON calls(symbol, timeframe, clicked_time) WHERE source = 'ai'"
            )

    # ------------------------------------------------------------------ #

    def add(self, call: TrackedCall) -> TrackedCall | None:
        """Insert a call. Returns None if it was a duplicate AI pick (deduped)."""
        row = asdict(call)
        cols = ", ".join(_COLUMNS)
        marks = ", ".join(f":{c}" for c in _COLUMNS)
        try:
            with _LOCK, self._connect() as conn:
                conn.execute(f"INSERT INTO calls ({cols}) VALUES ({marks})", row)
            return call
        except sqlite3.IntegrityError:
            return None  # duplicate AI pick for this candle — that's fine

    def all(self) -> list[TrackedCall]:
        with _LOCK, self._connect() as conn:
            rows = conn.execute("SELECT * FROM calls ORDER BY created_at ASC").fetchall()
        return [TrackedCall(**dict(r)) for r in rows]

    def update(self, call: TrackedCall) -> None:
        with _LOCK, self._connect() as conn:
            conn.execute(
                "UPDATE calls SET status=:status, resolved_time=:resolved_time, "
                "resolved_price=:resolved_price, r_multiple=:r_multiple WHERE id=:id",
                asdict(call),
            )

    def save_all(self, calls: list[TrackedCall]) -> None:
        with _LOCK, self._connect() as conn:
            conn.executemany(
                "UPDATE calls SET status=:status, resolved_time=:resolved_time, "
                "resolved_price=:resolved_price, r_multiple=:r_multiple WHERE id=:id",
                [asdict(c) for c in calls],
            )

    def clear(self) -> None:
        with _LOCK, self._connect() as conn:
            conn.execute("DELETE FROM calls")

    def remove_last(self, n: int) -> int:
        """Delete the ``n`` most recently created calls. Returns how many were removed."""
        with _LOCK, self._connect() as conn:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM calls ORDER BY created_at DESC LIMIT ?", (n,)
                ).fetchall()
            ]
            if ids:
                conn.executemany("DELETE FROM calls WHERE id = ?", [(i,) for i in ids])
        return len(ids)


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
