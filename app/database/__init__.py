"""Persistence layer for the long-term prediction-history database.

Exposes the connection helper and the versioned migration runner used by
``data/prediction_history.db`` — the permanent store that Forward Testing writes to
today, and that Historical Memory, the Learning Engine, the Similarity Engine and the
Model Registry will extend (via new migrations) in later milestones.
"""

from __future__ import annotations

from app.database.connection import DEFAULT_DB_PATH, get_connection
from app.database.migrations import (
    MIGRATIONS,
    Migration,
    applied_versions,
    initialize_database,
    run_migrations,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "get_connection",
    "MIGRATIONS",
    "Migration",
    "applied_versions",
    "initialize_database",
    "run_migrations",
]
