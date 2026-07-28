"""Migration + schema tests for Historical Memory (Sprint 2 · Milestone 1).

These verify the satellite schema is created correctly and, critically, that adding it
**does not touch Sprint 1**: the ``predictions`` table's definition and data are unchanged,
migrations are idempotent, and every Sprint 2 object is a new table/index that can be dropped
cleanly (rollback safety). All tests use temporary databases; production data is never used.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.database.connection import get_connection
from app.database import migrations as mig
from app.database.migrations import MIGRATIONS, Migration, applied_versions, run_migrations
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.models import (
    AggregateDimension,
    MemoryAggregate,
    MemoryEmbedding,
    MemoryReasoning,
)

# Column contracts the models depend on (schema ↔ model alignment).
_REASONING_COLS = {
    "prediction_id", "created_at", "confidence", "rationale",
    "factors_json", "rule_check_json", "schema_version",
}
_EMBEDDING_COLS = {
    "embedding_id", "prediction_id", "embedding_kind", "model_name",
    "dim", "vector", "created_at", "schema_version",
}
_AGGREGATE_COLS = {
    "dimension", "bucket", "model_version", "n_resolved", "wins", "losses",
    "win_rate", "avg_r", "expectancy", "total_r", "profit_factor",
    "max_drawdown_r", "avg_holding_bars", "updated_at",
}
_NEW_MEMORY_INDEXES = {
    "idx_mem_reasoning_confidence", "idx_mem_emb_once", "idx_mem_emb_kind",
    "idx_mem_agg_dimension",
}
_NEW_PREDICTION_INDEXES = {
    "idx_pred_sector_status", "idx_pred_regime_status",
    "idx_pred_predmodel_status", "idx_pred_timeframe_created",
}
_SPRINT1_PREDICTION_INDEXES = {"idx_pred_once", "idx_pred_status", "idx_pred_symbol_created"}


# --------------------------------------------------------------------------- helpers
def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
    return {r["name"] for r in rows}


def _table_schema(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone()
    return row["sql"] if row else ""


def _insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = ", ".join(row)
    placeholders = ", ".join(f":{k}" for k in row)
    with conn:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", row)


def _sample_prediction(symbol: str = "RELIANCE.NS") -> PredictionRecord:
    return PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe="1d", current_price=100.0,
        direction="BUY", recommendation="BUY", created_candle_ts=1_700_000_000,
        entry=100.0, stop=95.0, target1=110.0, sector="Energy", market_regime="BULL",
        prediction_model_version="pred-2025-11", feature_version="feat-v3",
        status=PredictionStatus.ACTIVE,
    )


def _apply_only_v1(path: str) -> sqlite3.Connection:
    """Bring a database to the *Sprint 1 only* state (migration 0001 applied)."""
    conn = get_connection(path)
    applied_versions(conn)  # ensures schema_migrations exists
    m1 = next(m for m in MIGRATIONS if m.version == 1)
    conn.executescript(m1.sql)
    conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (1, ?, ?)",
        (m1.name, "2020-01-01T00:00:00+00:00"),
    )
    conn.commit()
    return conn


# --------------------------------------------------------------------------- fresh DB
def test_fresh_db_applies_all_migrations(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    # Robust to appended migrations (e.g. Sprint 4's learning tables) — must include 1..5.
    assert {1, 2, 3, 4, 5} <= applied_versions(conn) == {m.version for m in MIGRATIONS}


def test_fresh_db_creates_satellite_tables_with_expected_columns(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    assert _columns(conn, "memory_reasoning") == _REASONING_COLS
    assert _columns(conn, "memory_embeddings") == _EMBEDDING_COLS
    assert _columns(conn, "memory_aggregates") == _AGGREGATE_COLS


def test_fresh_db_creates_all_new_indexes(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    indexes = _objects(conn, "index")
    assert _NEW_MEMORY_INDEXES <= indexes
    assert _NEW_PREDICTION_INDEXES <= indexes
    # Sprint 1 indexes are still present (untouched).
    assert _SPRINT1_PREDICTION_INDEXES <= indexes


def test_memory_aggregates_primary_key_is_composite(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    pk_cols = {r["name"] for r in conn.execute("PRAGMA table_info(memory_aggregates)") if r["pk"]}
    assert pk_cols == {"dimension", "bucket", "model_version"}


# --------------------------------------------------------------------------- idempotency
def test_migrations_are_idempotent(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    first = run_migrations(conn)
    second = run_migrations(conn)
    assert first == sorted(m.version for m in MIGRATIONS)   # all applied, in order
    assert second == []                      # nothing re-applied
    assert applied_versions(conn) == {m.version for m in MIGRATIONS}


def test_rerun_preserves_tables_and_data(tmp_path):
    store = PredictionStore(path=str(tmp_path / "ph.db"))
    store.create(_sample_prediction())
    run_migrations(store._conn)              # re-run on a populated DB
    assert store.count() == 1                # data intact
    assert _columns(store._conn, "memory_reasoning") == _REASONING_COLS
    store.close()


# ------------------------------------------------- populated Sprint-1 DB → upgrade
def test_upgrade_from_sprint1_leaves_predictions_unchanged(tmp_path):
    path = str(tmp_path / "ph.db")
    conn = _apply_only_v1(path)

    # Sprint-1 state: only predictions exists, no memory tables yet.
    assert "memory_reasoning" not in _objects(conn, "table")
    _insert(conn, "predictions", _sample_prediction().to_row())

    schema_before = _table_schema(conn, "predictions")
    cols_before = list(conn.execute("PRAGMA table_info(predictions)"))
    row_before = dict(conn.execute("SELECT * FROM predictions").fetchone())

    # Upgrade forward (everything after the Sprint-1 baseline).
    applied = run_migrations(conn)
    assert applied == sorted(m.version for m in MIGRATIONS if m.version > 1)

    # The predictions TABLE definition and its data are byte-for-byte unchanged.
    assert _table_schema(conn, "predictions") == schema_before
    assert [dict(r) for r in conn.execute("PRAGMA table_info(predictions)")] == [dict(r) for r in cols_before]
    assert dict(conn.execute("SELECT * FROM predictions").fetchone()) == row_before

    # And the satellite tables now exist.
    assert {"memory_reasoning", "memory_embeddings", "memory_aggregates"} <= _objects(conn, "table")
    conn.close()


def test_upgrade_preserves_existing_prediction_indexes(tmp_path):
    conn = _apply_only_v1(str(tmp_path / "ph.db"))
    before = _objects(conn, "index") & _SPRINT1_PREDICTION_INDEXES
    run_migrations(conn)
    after = _objects(conn, "index")
    assert before <= after                   # none dropped
    assert _NEW_PREDICTION_INDEXES <= after   # new ones added
    conn.close()


# --------------------------------------------------------------------------- rollback safety
def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path, monkeypatch):
    """A migration that errors must roll back, leaving the DB in its prior valid state."""
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)                      # 1..5 good
    _insert(conn, "predictions", _sample_prediction().to_row())

    # A single failing statement — the runner must raise, leave it UNRECORDED (so a
    # corrected re-run retries it), and not corrupt existing data. (Migrations use
    # `executescript`, which autocommits each statement, so fault injection is a single
    # statement to keep the failure atomic.)
    bad = Migration(version=999, name="deliberately_bad", sql="INSERT INTO does_not_exist_table VALUES (1);")
    monkeypatch.setattr(mig, "MIGRATIONS", MIGRATIONS + (bad,))

    with pytest.raises(sqlite3.Error):
        run_migrations(conn)

    assert 999 not in applied_versions(conn)                 # not recorded → will be retried
    assert conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"] == 1
    # The already-applied Sprint 2 tables are untouched by the failed migration.
    assert {"memory_reasoning", "memory_embeddings", "memory_aggregates"} <= _objects(conn, "table")
    conn.close()


def test_satellites_drop_cleanly_without_touching_predictions(tmp_path):
    """Rollback posture: every Sprint 2 object is droppable with zero impact on Sprint 1."""
    store = PredictionStore(path=str(tmp_path / "ph.db"))
    pred = _sample_prediction()
    store.create(pred)
    conn = store._conn
    _insert(conn, "memory_reasoning", MemoryReasoning(pred.prediction_id, confidence=0.6).to_row())

    schema_before = _table_schema(conn, "predictions")
    with conn:
        for tbl in ("memory_reasoning", "memory_embeddings", "memory_aggregates"):
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        for idx in _NEW_PREDICTION_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {idx}")

    # Predictions and Sprint 1 behaviour are completely intact after the "rollback".
    assert _table_schema(conn, "predictions") == schema_before
    assert store.count() == 1
    assert store.list_active()[0].symbol == "RELIANCE.NS"
    assert _SPRINT1_PREDICTION_INDEXES <= _objects(conn, "index")
    store.close()


# ------------------------------------------------- schema ↔ model round-trips
def test_reasoning_row_round_trips(tmp_path):
    store = PredictionStore(path=str(tmp_path / "ph.db"))
    pred = _sample_prediction()
    store.create(pred)
    original = MemoryReasoning(
        prediction_id=pred.prediction_id, confidence=0.62,
        rationale="momentum + regime", factors={"trend": "up"}, rule_check={"rr_ok": True},
    )
    _insert(store._conn, "memory_reasoning", original.to_row())
    row = store._conn.execute("SELECT * FROM memory_reasoning WHERE prediction_id = ?", (pred.prediction_id,)).fetchone()
    got = MemoryReasoning.from_row(row)
    assert got.confidence == 0.62
    assert got.factors == {"trend": "up"} and got.rule_check == {"rr_ok": True}
    store.close()


def test_embedding_vector_round_trips(tmp_path):
    store = PredictionStore(path=str(tmp_path / "ph.db"))
    pred = _sample_prediction()
    store.create(pred)
    emb = MemoryEmbedding(prediction_id=pred.prediction_id, model_name="e5", vector=[0.1, -0.2, 0.3])
    _insert(store._conn, "memory_embeddings", emb.to_row())
    row = store._conn.execute("SELECT * FROM memory_embeddings WHERE prediction_id = ?", (pred.prediction_id,)).fetchone()
    got = MemoryEmbedding.from_row(row)
    assert got.dim == 3
    assert got.vector == pytest.approx([0.1, -0.2, 0.3], abs=1e-6)
    store.close()


def test_embedding_slot_can_be_empty(tmp_path):
    """The placeholder must persist with no vector (the initial state until Similarity lands)."""
    store = PredictionStore(path=str(tmp_path / "ph.db"))
    pred = _sample_prediction()
    store.create(pred)
    _insert(store._conn, "memory_embeddings", MemoryEmbedding(prediction_id=pred.prediction_id).to_row())
    row = store._conn.execute("SELECT * FROM memory_embeddings WHERE prediction_id = ?", (pred.prediction_id,)).fetchone()
    assert MemoryEmbedding.from_row(row).vector is None
    store.close()


def test_aggregate_row_round_trips(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    agg = MemoryAggregate(
        dimension=AggregateDimension.SECTOR, bucket="Energy", model_version="pred-2025-11",
        n_resolved=5, wins=3, losses=2, win_rate=0.6, avg_r=0.4, expectancy=0.4,
        total_r=2.0, profit_factor=1.8, max_drawdown_r=1.1, avg_holding_bars=6.0,
    )
    _insert(conn, "memory_aggregates", agg.to_row())
    row = conn.execute("SELECT * FROM memory_aggregates WHERE dimension='sector' AND bucket='Energy'").fetchone()
    got = MemoryAggregate.from_row(row)
    assert got.dimension is AggregateDimension.SECTOR
    assert got.n_resolved == 5 and got.win_rate == 0.6
    conn.close()


def test_embedding_unique_per_prediction_and_kind(tmp_path):
    store = PredictionStore(path=str(tmp_path / "ph.db"))
    pred = _sample_prediction()
    store.create(pred)
    _insert(store._conn, "memory_embeddings", MemoryEmbedding(pred.prediction_id, embedding_kind="context_v1").to_row())
    with pytest.raises(sqlite3.IntegrityError):
        _insert(store._conn, "memory_embeddings", MemoryEmbedding(pred.prediction_id, embedding_kind="context_v1").to_row())
    store.close()


def test_memory_models_do_not_import_engines():
    import ast

    import app.memory.models as mm
    with open(mm.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
