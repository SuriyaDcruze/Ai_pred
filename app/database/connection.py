"""SQLite connection management for the long-term prediction-history store.

``prediction_history.db`` is the **permanent** persistence layer for the platform's
memory: Forward Testing today, and later Historical Memory, the Learning Engine, the
Similarity Engine, GPT conversation history, and the Model Registry. It is deliberately
separate from the legacy ``calls.db`` (the You-vs-AI paper tracker), which has a
different lifecycle and is left untouched.

Raw ``sqlite3`` is used (matching ``app/tracking/tracker.py``) — no new dependencies, and
simple enough for a modular monolith. The optional SQLAlchemy models in
``app/database/models.py`` remain the future Postgres path (Architecture Book Vol 21);
nothing imports them today.

Concurrency: the Forward-Testing monitor (a later milestone) writes while the API reads,
so connections are opened in **WAL** mode with a ``busy_timeout``. WAL lets readers and a
writer proceed concurrently instead of blocking on a global lock.
"""

from __future__ import annotations

import os
import sqlite3

#: Default location of the permanent prediction-history database.
DEFAULT_DB_PATH: str = os.path.join("data", "prediction_history.db")

#: How long (ms) a blocked writer waits for a lock before raising.
_BUSY_TIMEOUT_MS: int = 5_000


def get_connection(path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (and prepare) a connection to the prediction-history database.

    Creates the parent directory if needed, enables WAL for concurrent readers, sets a
    busy timeout so a momentarily-locked write retries instead of failing, and returns
    rows as :class:`sqlite3.Row` so callers read **by column name** — which keeps future
    schema additions backward compatible.

    Args:
        path: Database file path. Defaults to :data:`DEFAULT_DB_PATH`.

    Returns:
        A configured :class:`sqlite3.Connection`.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    # check_same_thread=False lets the background monitor (which runs a pass in a worker
    # thread) share this connection with the request thread. Concurrent access is
    # serialised by a lock in PredictionStore, and WAL keeps readers off the writer's back.
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL: concurrent readers + one writer (the monitor) without global locking.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
