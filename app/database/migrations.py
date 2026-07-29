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


# --------------------------------------------------------------------------- #
# Sprint 2 · Milestone 1 — Historical Memory schema (satellite tables + indexes).
#
# Historical Memory does NOT copy the predictions table. The `predictions` row remains the
# single source of truth for everything it already stores; these tables hold only what
# `predictions` deliberately does not — the reasoning narrative, vector embeddings, and
# derived rollups — keyed back to a prediction by `prediction_id`. This "satellite" design
# keeps `predictions` immutable and untouched, makes every change purely additive, and makes
# rollback a clean table drop. See sprints/sprint-02-historical-memory-plan.md §4.
# --------------------------------------------------------------------------- #

# 0002 — memory_reasoning: the "why" behind one decision (1:1 with a prediction).
# `predictions` stores the numeric confidence; this stores the human/structured explanation.
_0002_CREATE_MEMORY_REASONING = """
CREATE TABLE IF NOT EXISTS memory_reasoning (
    prediction_id   TEXT PRIMARY KEY,
    created_at      TEXT    NOT NULL,
    confidence      REAL,                       -- mirror of decision-time confidence (indexable)
    rationale       TEXT,                       -- free-text "why"
    factors_json    TEXT,                       -- structured drivers: {factor: contribution/label}
    rule_check_json TEXT,                       -- snapshot of the My-Rules checklist result
    schema_version  INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (prediction_id) REFERENCES predictions(prediction_id)
);

-- "search by confidence" without scanning the reasoning JSON.
CREATE INDEX IF NOT EXISTS idx_mem_reasoning_confidence ON memory_reasoning(confidence);
"""

# 0003 — memory_embeddings: vector placeholder for the future Similarity Engine (Vol 14).
# Nothing computes embeddings in Sprint 2; this owns the storage + contract. Multiple
# `embedding_kind`s per prediction are allowed so a new embedding model can coexist with an
# old one (no destructive recompute).
_0003_CREATE_MEMORY_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS memory_embeddings (
    embedding_id   TEXT PRIMARY KEY,
    prediction_id  TEXT    NOT NULL,
    embedding_kind TEXT    NOT NULL,            -- e.g. 'context_v1'
    model_name     TEXT,                        -- which embedding model produced the vector
    dim            INTEGER,                      -- vector dimensionality
    vector         BLOB,                         -- packed float32; NULL until populated
    created_at     TEXT    NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (prediction_id) REFERENCES predictions(prediction_id)
);

-- one vector per (prediction, kind); lookups by kind for retrieval.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_emb_once ON memory_embeddings(prediction_id, embedding_kind);
CREATE INDEX IF NOT EXISTS idx_mem_emb_kind ON memory_embeddings(embedding_kind);
"""

# 0004 — memory_aggregates: derived performance rollups for Performance Analytics.
# Fully derivable from `predictions` (droppable + rebuildable), so it is never a source of
# truth. Keyed by model_version so a model swap never blends two models into one number.
_0004_CREATE_MEMORY_AGGREGATES = """
CREATE TABLE IF NOT EXISTS memory_aggregates (
    dimension        TEXT    NOT NULL,          -- overall|symbol|sector|timeframe|regime|confidence_bucket|outcome
    bucket           TEXT    NOT NULL,          -- the value within the dimension
    model_version    TEXT    NOT NULL DEFAULT '', -- '' = across all models
    n_resolved       INTEGER NOT NULL DEFAULT 0,
    wins             INTEGER NOT NULL DEFAULT 0,
    losses           INTEGER NOT NULL DEFAULT 0,
    win_rate         REAL,
    avg_r            REAL,
    expectancy       REAL,
    total_r          REAL,
    profit_factor    REAL,
    max_drawdown_r   REAL,
    avg_holding_bars REAL,
    updated_at       TEXT    NOT NULL,
    PRIMARY KEY (dimension, bucket, model_version)
);

CREATE INDEX IF NOT EXISTS idx_mem_agg_dimension ON memory_aggregates(dimension);
"""

# 0005 — additive retrieval indexes on the existing predictions table. Indexes are pure
# metadata: they add no column, change no row, and cannot alter a prediction's result — so
# this is an *additive* migration, not a modification of the Sprint 1 schema. Existing
# indexes (from 0001) are left untouched. These support Historical Memory's retrieval paths
# (search by sector / regime / model version / timeframe).
_0005_MEMORY_RETRIEVAL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_pred_sector_status        ON predictions(sector, status);
CREATE INDEX IF NOT EXISTS idx_pred_regime_status        ON predictions(market_regime, status);
CREATE INDEX IF NOT EXISTS idx_pred_predmodel_status     ON predictions(prediction_model_version, status);
CREATE INDEX IF NOT EXISTS idx_pred_timeframe_created    ON predictions(timeframe, created_at);
"""


# --------------------------------------------------------------------------- #
# Sprint 4 · Milestone 1 — Behavioural Learning storage (foundation).
#
# The Behavioural Learning Engine (Vol 15, Sprint 4) is descriptive analytics over completed
# Historical Memory — no training, no prediction, read-only over predictions/memory. Its
# artifacts live in **their own** learning tables in this same database (ADR 0005), added by
# append-only migrations; no Sprint 1–3 table is ever changed. `learning_runs` records the
# metadata of one analysis/dataset run (audit + reproducibility); pattern/recommendation
# tables arrive in their own milestones.
# --------------------------------------------------------------------------- #
_0006_CREATE_LEARNING_RUNS = """
CREATE TABLE IF NOT EXISTS learning_runs (
    run_id               TEXT    PRIMARY KEY,
    kind                 TEXT    NOT NULL,          -- 'dataset' (later: 'analysis')
    learning_version     TEXT    NOT NULL,
    dataset_version      TEXT    NOT NULL,
    created_at           TEXT    NOT NULL,
    corpus_size          INTEGER NOT NULL,
    checksum             TEXT,                       -- deterministic dataset fingerprint
    status               TEXT,                       -- VALIDATED | HYPOTHESIS | INSUFFICIENT_DATA | NULL
    params_json          TEXT,
    source_versions_json TEXT,
    build_duration_ms    REAL
);

CREATE INDEX IF NOT EXISTS idx_learning_runs_created ON learning_runs(created_at);
"""

# 0007 — learning_patterns: candidate behavioural patterns (Sprint 4 · M2). **Metadata only**
# — grouping key/value + evidence references + counts. NO statistics/confidence/recommendations
# (those are later milestones, in their own columns/tables). Its own table; no Sprint 1–3 table
# is changed. Derived + rebuildable from the Learning Dataset.
_0007_CREATE_LEARNING_PATTERNS = """
CREATE TABLE IF NOT EXISTS learning_patterns (
    pattern_id           TEXT    PRIMARY KEY,
    run_id               TEXT,                       -- FK to learning_runs (set when persisted)
    learning_version     TEXT    NOT NULL,
    dataset_version      TEXT    NOT NULL,
    pattern_type         TEXT    NOT NULL,           -- SETUP | MARKET | CONFIDENCE | MODEL | HOLDING | OUTCOME | INSTRUMENT
    grouping_key         TEXT    NOT NULL,           -- the dimension (e.g. 'sector')
    grouping_value       TEXT    NOT NULL,           -- the bucket (e.g. 'Energy')
    evidence_count       INTEGER NOT NULL,
    prediction_ids_json  TEXT,                        -- evidence: the supporting prediction ids
    corpus_size          INTEGER,
    status               TEXT,                        -- HYPOTHESIS (M2) — never VALIDATED yet
    created_at           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_patterns_run ON learning_patterns(run_id);
CREATE INDEX IF NOT EXISTS idx_learning_patterns_key ON learning_patterns(grouping_key);
"""

# 0008 — learning_pattern_stats: statistically **validated** learning artifacts (Sprint 4 · M3).
# One row per candidate pattern that went through the Statistical Validation Engine: descriptive
# statistics (reusing the Sprint 2 aggregate math), a 95% confidence interval, a significance
# test, and the multiple-comparison correction outcome — plus the resulting lifecycle status
# (VALIDATED | HYPOTHESIS | INSUFFICIENT_DATA). Its own table; **no Sprint 1–3 table is changed**.
# Derived + rebuildable from the Learning Dataset + candidate patterns; the validator is
# read-only, so nothing is written here until a later (persisting) milestone.
_0008_CREATE_LEARNING_PATTERN_STATS = """
CREATE TABLE IF NOT EXISTS learning_pattern_stats (
    pattern_key            TEXT    PRIMARY KEY,
    run_id                 TEXT,                       -- FK to learning_runs (set when persisted)
    learning_version       TEXT    NOT NULL,
    dataset_version        TEXT    NOT NULL,
    pattern_type           TEXT,
    grouping_key           TEXT    NOT NULL,
    grouping_value         TEXT    NOT NULL,
    sample_size            INTEGER NOT NULL,
    wins                   INTEGER,
    losses                 INTEGER,
    win_rate               REAL,
    loss_rate              REAL,
    average_r              REAL,
    expectancy             REAL,
    profit_factor          REAL,
    max_drawdown_r         REAL,
    avg_holding_bars       REAL,
    ci_low                 REAL,                       -- 95% Wilson interval on win rate
    ci_high                REAL,
    ci_width               REAL,
    ci_quality             TEXT,                       -- HIGH | MODERATE | LOW (by width)
    p_value                REAL,                       -- raw two-sided proportion test vs baseline
    z_score                REAL,
    baseline               REAL,
    significant            INTEGER,                    -- raw significance (pre-correction), 0/1
    correction_method      TEXT,                       -- benjamini_hochberg | bonferroni | none
    correction_significant INTEGER,                    -- significant AFTER multiple-comparison, 0/1
    consistency_score      REAL,                       -- sub-period stability (1 = stable), or NULL
    status                 TEXT,                        -- VALIDATED | HYPOTHESIS | INSUFFICIENT_DATA
    evidence_count         INTEGER,
    created_at             TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_stats_run    ON learning_pattern_stats(run_id);
CREATE INDEX IF NOT EXISTS idx_learning_stats_key    ON learning_pattern_stats(grouping_key);
CREATE INDEX IF NOT EXISTS idx_learning_stats_status ON learning_pattern_stats(status);
"""


#: All migrations, in ascending version order. **Append only.**
MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, name="create_predictions", sql=_0001_CREATE_PREDICTIONS),
    Migration(version=2, name="create_memory_reasoning", sql=_0002_CREATE_MEMORY_REASONING),
    Migration(version=3, name="create_memory_embeddings", sql=_0003_CREATE_MEMORY_EMBEDDINGS),
    Migration(version=4, name="create_memory_aggregates", sql=_0004_CREATE_MEMORY_AGGREGATES),
    Migration(version=5, name="memory_retrieval_indexes", sql=_0005_MEMORY_RETRIEVAL_INDEXES),
    Migration(version=6, name="create_learning_runs", sql=_0006_CREATE_LEARNING_RUNS),
    Migration(version=7, name="create_learning_patterns", sql=_0007_CREATE_LEARNING_PATTERNS),
    Migration(version=8, name="create_learning_pattern_stats", sql=_0008_CREATE_LEARNING_PATTERN_STATS),
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
