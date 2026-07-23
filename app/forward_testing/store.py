"""Prediction Store — persistence and retrieval for forward-tested predictions.

The store is the only component that touches the ``predictions`` table. It owns four
guarantees that the (later) background monitor depends on:

* **Duplicate protection.** Creation relies on the unique index
  ``(symbol, timeframe, created_candle_ts, source)``: a second attempt for the same bar
  returns ``None`` instead of raising, so a monitor that runs twice records once.
* **Audit-friendly writes.** Only lifecycle columns (``status``, ``resolved_*``,
  ``realised_r``, ``holding_bars``, ``updated_at``) are ever updated. Everything captured
  at creation is immutable — the record of what the models actually said cannot be
  rewritten after the fact.
* **Idempotent updates.** A prediction already in a terminal state is never re-resolved;
  repeat calls are no-ops that report ``False``.
* **Restart safety.** No state is held in memory. :meth:`PredictionStore.list_active`
  re-reads open predictions from the database, so a process restart resumes exactly where
  it left off.

The store is **read-only with respect to the Prediction and Outcome engines** — it
persists their outputs and imports nothing from them.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Iterable

from app.database.connection import DEFAULT_DB_PATH, get_connection
from app.database.migrations import run_migrations
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Columns the store is allowed to modify after a record is created.
_MUTABLE_COLUMNS: frozenset[str] = frozenset(
    {
        "status",
        "resolved_at",
        "resolved_price",
        "resolution_reason",
        "realised_r",
        "holding_bars",
        "updated_at",
    }
)


class PredictionStore:
    """CRUD, queries and aggregate statistics over the ``predictions`` table."""

    def __init__(self, path: str = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None):
        """Open the store, ensuring the schema is current.

        Args:
            path: Database path; defaults to the permanent prediction-history database.
            conn: An existing connection to adopt (mainly for tests); when given,
                ``path`` is ignored.
        """
        self._conn = conn or get_connection(path)
        # Serialises access so the background monitor's worker thread and the request
        # thread never touch the shared connection concurrently. Reentrant because some
        # methods (update_*) call get() while already holding the lock.
        self._lock = threading.RLock()
        run_migrations(self._conn)

    # ------------------------------------------------------------------ create
    def create(self, record: PredictionRecord) -> PredictionRecord | None:
        """Persist a new prediction.

        Args:
            record: The record to store.

        Returns:
            The stored record, or ``None`` if an identical prediction (same symbol,
            timeframe, origin candle and source) already exists — duplicate protection,
            not an error.
        """
        row = record.to_row()
        columns = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        try:
            with self._lock, self._conn:  # lock + transaction (commit / rollback)
                self._conn.execute(
                    f"INSERT INTO predictions ({columns}) VALUES ({placeholders})", row
                )
        except sqlite3.IntegrityError:
            logger.debug(
                "duplicate prediction ignored: %s %s @%s (%s)",
                record.symbol, record.timeframe, record.created_candle_ts, record.source,
            )
            return None
        return record

    # -------------------------------------------------------------------- read
    def get(self, prediction_id: str) -> PredictionRecord | None:
        """Fetch one prediction by id, or ``None`` if it does not exist."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM predictions WHERE prediction_id = ?", (prediction_id,)
            ).fetchone()
        return PredictionRecord.from_row(row) if row else None

    def list_active(self, symbol: str | None = None) -> list[PredictionRecord]:
        """Open predictions (PENDING / ENTRY_TRIGGERED / ACTIVE), oldest first.

        This is the restart-recovery entry point: the monitor calls it on startup to
        resume watching everything the market has not yet resolved.
        """
        return self._query_by_status(PredictionStatus.open_states(), symbol=symbol)

    def list_completed(
        self, limit: int | None = None, symbol: str | None = None
    ) -> list[PredictionRecord]:
        """Resolved predictions (terminal states), newest first."""
        records = self._query_by_status(
            PredictionStatus.terminal_states(), symbol=symbol, newest_first=True
        )
        return records[:limit] if limit else records

    def list_all(self, limit: int | None = None) -> list[PredictionRecord]:
        """Every prediction, newest first (diagnostics and exports)."""
        sql = "SELECT * FROM predictions ORDER BY created_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            return [PredictionRecord.from_row(r) for r in self._conn.execute(sql)]

    def _query_by_status(
        self,
        statuses: Iterable[PredictionStatus],
        *,
        symbol: str | None = None,
        newest_first: bool = False,
    ) -> list[PredictionRecord]:
        """Shared status query — keeps the SQL in one place."""
        values = [s.value for s in statuses]
        sql = f"SELECT * FROM predictions WHERE status IN ({', '.join('?' * len(values))})"
        params: list[Any] = list(values)
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        sql += " ORDER BY created_at DESC" if newest_first else " ORDER BY created_at ASC"
        with self._lock:
            return [PredictionRecord.from_row(r) for r in self._conn.execute(sql, params)]

    # ------------------------------------------------------------------ update
    def update_status(self, prediction_id: str, status: PredictionStatus) -> bool:
        """Move a prediction to a new lifecycle state.

        Idempotent: a prediction already in a terminal state is never modified.

        Returns:
            ``True`` when the status changed, ``False`` when the record is missing,
            already terminal, or already in that state.
        """
        with self._lock:  # atomic read-modify-write (RLock → nested get/write is fine)
            current = self.get(prediction_id)
            if current is None:
                logger.debug("update_status: unknown prediction %s", prediction_id)
                return False
            if current.is_terminal():
                logger.debug("update_status: %s already terminal (%s)", prediction_id, current.status)
                return False
            if current.status is status:
                return False
            return self._write_lifecycle(prediction_id, {"status": status.value})

    def update_resolution(
        self,
        prediction_id: str,
        *,
        status: PredictionStatus,
        resolved_price: float,
        resolution_reason: str,
        realised_r: float,
        holding_bars: int,
        resolved_at: str | None = None,
    ) -> bool:
        """Record the outcome of a prediction and close it.

        Writes only lifecycle columns, so the original prediction stays immutable.
        Idempotent: an already-resolved prediction is left untouched.

        Args:
            prediction_id: Which prediction to resolve.
            status: The terminal state reached (TARGET_HIT / STOP_HIT / EXPIRED / …).
            resolved_price: Price at resolution.
            resolution_reason: Human-readable reason ("target hit", "stop hit", "expired").
            realised_r: Realised R-multiple.
            holding_bars: How many bars the position was held.
            resolved_at: Optional ISO timestamp; defaults to now.

        Returns:
            ``True`` when the record was resolved by this call, else ``False``.
        """
        if not status.is_terminal():
            raise ValueError(f"{status} is not a terminal state")

        from app.forward_testing.models import _utc_now_iso

        with self._lock:  # atomic read-modify-write
            current = self.get(prediction_id)
            if current is None:
                logger.debug("update_resolution: unknown prediction %s", prediction_id)
                return False
            if current.is_terminal():
                logger.debug("update_resolution: %s already resolved", prediction_id)
                return False
            return self._write_lifecycle(
                prediction_id,
                {
                    "status": status.value,
                    "resolved_at": resolved_at or _utc_now_iso(),
                    "resolved_price": resolved_price,
                    "resolution_reason": resolution_reason,
                    "realised_r": realised_r,
                    "holding_bars": holding_bars,
                },
            )

    def _write_lifecycle(self, prediction_id: str, changes: dict[str, Any]) -> bool:
        """Apply a lifecycle-only update inside a transaction.

        Guards the audit rule: any attempt to modify a creation column is a programming
        error and raises rather than silently rewriting history.
        """
        illegal = set(changes) - _MUTABLE_COLUMNS
        if illegal:
            raise ValueError(f"immutable columns cannot be updated: {sorted(illegal)}")

        from app.forward_testing.models import _utc_now_iso

        changes = {**changes, "updated_at": _utc_now_iso()}
        assignments = ", ".join(f"{col} = :{col}" for col in changes)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"UPDATE predictions SET {assignments} WHERE prediction_id = :prediction_id",
                {**changes, "prediction_id": prediction_id},
            )
        return cursor.rowcount > 0

    # -------------------------------------------------------------- statistics
    def count(self, status: PredictionStatus | None = None) -> int:
        """Number of predictions, optionally filtered by status."""
        with self._lock:
            if status is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM predictions WHERE status = ?", (status.value,)
                ).fetchone()
        return int(row["n"])

    def statistics(self, symbol: str | None = None) -> dict[str, Any]:
        """Aggregate performance of the resolved predictions.

        All figures derive from realised R-multiples, so they are position-size agnostic.
        A small sample is reported honestly via ``resolved`` — callers must not present a
        win rate from a handful of trades as proof of anything.

        Returns:
            Counts (total / open / resolved), win rate, average R, total R, profit factor,
            maximum drawdown (in R, from the equity curve of realised trades), average
            holding bars, and current open risk in R.
        """
        resolved = [
            r for r in self.list_completed(symbol=symbol) if r.realised_r is not None
        ]
        open_records = self.list_active(symbol=symbol)

        stats: dict[str, Any] = {
            "total": self.count() if symbol is None else len(resolved) + len(open_records),
            "open": len(open_records),
            "resolved": len(resolved),
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "avg_r": None,
            "total_r": 0.0,
            "profit_factor": None,
            "max_drawdown_r": 0.0,
            "avg_holding_bars": None,
            "open_risk_r": float(len(open_records)),  # each open trade risks 1R
        }
        if not resolved:
            return stats

        r_values = [float(r.realised_r) for r in resolved]
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r < 0]

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))

        stats["wins"] = len(wins)
        stats["losses"] = len(losses)
        stats["win_rate"] = len(wins) / len(r_values)
        stats["avg_r"] = sum(r_values) / len(r_values)
        stats["total_r"] = sum(r_values)
        stats["profit_factor"] = (
            gross_profit / gross_loss if gross_loss > 0 else (None if not wins else float("inf"))
        )
        stats["max_drawdown_r"] = self._max_drawdown(r_values)

        holdings = [r.holding_bars for r in resolved if r.holding_bars is not None]
        if holdings:
            stats["avg_holding_bars"] = sum(holdings) / len(holdings)
        return stats

    @staticmethod
    def _max_drawdown(r_values: list[float]) -> float:
        """Largest peak-to-trough decline of the cumulative R curve (positive number)."""
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in r_values:
            equity += r
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return max_dd

    # ------------------------------------------------------------------ misc
    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "PredictionStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
