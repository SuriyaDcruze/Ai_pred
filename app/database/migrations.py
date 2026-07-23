"""Versioned, idempotent schema migrations for ``prediction_history.db``.

Design rules (Architecture Book Vol 21 — Database Design):

* **Forward-only and append-only.** A migration, once applied, is *never edited*. New
  schema arrives as a new numbered migration. This is what lets the database grow —
  Historical Memory, Learning Engine, Similarity Engine, Model Registry, GPT history all
  get their tables as future migrations — **without breaking compatibility**.
* **Idempotent.** :func:`run_migrations` is safe to call on every startup; already-applied
  versions are skipped.
* **Transactional.** Each migration runs inside a transaction and is recorded in
  ``schema_migrations`` only if it succeeds.

To add a table later, append a new ``Migration`` to :data:`MIGRATIONS` with the next
version number. Do not modify existing entries.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Migration:
    """A single, immutable schema change."""

    version: int
    name: str
    sql: str


# --------------------------------------------------------------------------- #
# 0001 — the predictions table (Forward Testing + the foundation of Historical
# Memory). Rich context and independent version columns are included from day one so
# stored predictions stay useful for future explainability and AI learning.
# --------------------------------------------------------------------------- #
_0001_CREATE_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id            TEXT PRIMARY KEY,
    created_at               TEXT    NOT NULL,
    updated_at               TEXT    NOT NULL,
    created_candle_ts        INTEGER NOT NULL,

    -- instrument
    symbol                   TEXT    NOT NULL,
    exchange                 TEXT    NOT NULL,
    timeframe                TEXT    NOT NULL,
    source                   TEXT    NOT NULL DEFAULT 'forward',

    -- prediction outputs (verbatim from the Prediction/Outcome engines)
    current_price            REAL    NOT NULL,
    direction                TEXT    NOT NULL,
    direction_prob           REAL,
    outcome_prob             REAL,
    decision_score           REAL,
    recommendation           TEXT    NOT NULL,

    -- trade plan (Risk Engine)
    entry                    REAL,
    stop                     REAL,
    target1                  REAL,
    target2                  REAL,

    -- rich market context (explainability + future learning)
    market_regime            TEXT,
    market_phase             TEXT,
    sector                   TEXT,
    session                  TEXT,
    volatility_bucket        TEXT,
    similarity_score         REAL,
    context_json             TEXT,

    -- independent version stamps (forward compatible)
    prediction_model_version TEXT,
    outcome_model_version    TEXT,
    feature_version          TEXT,

    -- lifecycle / resolution
    status                   TEXT    NOT NULL,
    resolved_at              TEXT,
    resolved_price           REAL,
    resolution_reason        TEXT,
    realised_r               REAL,
    holding_bars             INTEGER
);

-- One auto-created prediction per (instrument, origin bar, source): the duplicate
-- protection the background monitor relies on for idempotent creation.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pred_once
    ON predictions(symbol, timeframe, created_candle_ts, source);

-- The monitor scans open predictions; the UI lists recent ones per symbol.
CREATE INDEX IF NOT EXISTS idx_pred_status ON predictions(status);
CREATE INDEX IF NOT EXISTS idx_pred_symbol_created ON predictions(symbol, created_at);
"""


#: All migrations, in ascending version order. **Append only.**
MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, name="create_predictions", sql=_0001_CREATE_PREDICTIONS),
)


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create the bookkeeping table that records which migrations have run."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of migration versions already applied to this database."""
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(r["version"]) for r in rows}


def run_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply any pending migrations, in order. Safe to call on every startup.

    Args:
        conn: An open connection (see :func:`app.database.connection.get_connection`).

    Returns:
        The versions applied by *this* call (empty when the schema was already current).
    """
    done = applied_versions(conn)
    newly_applied: list[int] = []

    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version in done:
            continue
        try:
            conn.executescript(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    datetime.now(tz=timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("migration %04d (%s) failed", migration.version, migration.name)
            raise
        newly_applied.append(migration.version)
        logger.info("applied migration %04d — %s", migration.version, migration.name)

    return newly_applied


def initialize_database(path: str | None = None) -> sqlite3.Connection:
    """Open the prediction-history database and bring its schema up to date.

    Convenience entry point: callers get a ready-to-use connection without having to
    remember to run migrations.

    Args:
        path: Optional database path; defaults to the standard location.

    Returns:
        An open, migrated :class:`sqlite3.Connection`.
    """
    from app.database.connection import DEFAULT_DB_PATH, get_connection

    conn = get_connection(path or DEFAULT_DB_PATH)
    run_migrations(conn)
    return conn
