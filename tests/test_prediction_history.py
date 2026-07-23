"""Unit tests for the prediction-history foundation (Sprint 1 · Milestone 1).

Covers the two halves of the milestone:

* **Domain models** — the ``PredictionStatus`` lifecycle and the ``PredictionRecord``
  round-trip, including the rich context blob and the three independent version stamps.
* **Database** — the versioned migration runner, schema creation, idempotency, and the
  unique index that will give the (later) background monitor duplicate protection.

Every test uses a **temporary database** via ``tmp_path``; production data
(``data/prediction_history.db``) is never opened or modified.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.database.connection import get_connection
from app.database.migrations import (
    MIGRATIONS,
    applied_versions,
    initialize_database,
    run_migrations,
)
from app.forward_testing.models import PredictionRecord, PredictionStatus


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def db_path(tmp_path) -> str:
    """Path to a throwaway database file (never the production one)."""
    return str(tmp_path / "prediction_history.db")


@pytest.fixture
def conn(db_path) -> sqlite3.Connection:
    """An open, migrated connection to a temporary database."""
    connection = initialize_database(db_path)
    yield connection
    connection.close()


def _record(**overrides) -> PredictionRecord:
    """A fully-populated record, so round-trip tests exercise every field."""
    base = dict(
        symbol="RELIANCE.NS",
        exchange="yahoo",
        timeframe="1d",
        current_price=1296.60,
        direction="BUY",
        recommendation="BUY",
        created_candle_ts=1_700_000_000,
        direction_prob=0.62,
        outcome_prob=0.71,
        decision_score=0.68,
        entry=1296.60,
        stop=1270.00,
        target1=1336.50,
        target2=1363.10,
        market_regime="Strong Uptrend",
        market_phase="Trend Continuation",
        sector="Energy",
        session="IN",
        volatility_bucket="Normal",
        similarity_score=0.84,
        context={"nearest": 20, "win_rate": 0.63, "note": "sector strong"},
        prediction_model_version="logistic-BTCUSDT-1h-h12@abcd1234",
        outcome_model_version="outcome-nse-1d-h5@ef567890",
        feature_version="features-45@0f1e2d3c",
    )
    base.update(overrides)
    return PredictionRecord(**base)


# --------------------------------------------------------------------------- #
# PredictionStatus
# --------------------------------------------------------------------------- #

def test_status_has_the_eight_lifecycle_states():
    assert {s.value for s in PredictionStatus} == {
        "PENDING",
        "ENTRY_TRIGGERED",
        "ACTIVE",
        "TARGET_HIT",
        "STOP_HIT",
        "EXPIRED",
        "CANCELLED",
        "COMPLETED",
    }


def test_open_and_terminal_states_partition_the_enum():
    """Every state is either open or terminal — never both, never neither."""
    open_states = PredictionStatus.open_states()
    terminal = PredictionStatus.terminal_states()
    assert open_states & terminal == frozenset()
    assert open_states | terminal == set(PredictionStatus)


@pytest.mark.parametrize(
    "status",
    [PredictionStatus.PENDING, PredictionStatus.ENTRY_TRIGGERED, PredictionStatus.ACTIVE],
)
def test_open_states_are_open(status):
    assert status.is_open() is True
    assert status.is_terminal() is False


@pytest.mark.parametrize(
    "status",
    [
        PredictionStatus.TARGET_HIT,
        PredictionStatus.STOP_HIT,
        PredictionStatus.EXPIRED,
        PredictionStatus.CANCELLED,
        PredictionStatus.COMPLETED,
    ],
)
def test_terminal_states_are_terminal(status):
    assert status.is_terminal() is True
    assert status.is_open() is False


def test_status_is_a_string_enum_for_storage():
    """Stored as plain text, so the DB stays readable and portable."""
    assert PredictionStatus.ACTIVE == "ACTIVE"
    assert PredictionStatus("ACTIVE") is PredictionStatus.ACTIVE


# --------------------------------------------------------------------------- #
# PredictionRecord
# --------------------------------------------------------------------------- #

def test_new_record_starts_pending_and_open():
    record = _record()
    assert record.status is PredictionStatus.PENDING
    assert record.is_open() is True
    assert record.is_terminal() is False


def test_record_generates_id_and_timestamps():
    a, b = _record(), _record()
    assert a.prediction_id != b.prediction_id      # unique per record
    assert a.created_at and a.updated_at


def test_to_row_contains_every_schema_column(conn):
    """The row keys must match the migrated table's columns exactly."""
    row = _record().to_row()
    table_columns = {r["name"] for r in conn.execute("PRAGMA table_info(predictions)")}
    assert set(row) == table_columns


def test_round_trip_is_lossless():
    original = _record()
    restored = PredictionRecord.from_row(original.to_row())

    assert restored.prediction_id == original.prediction_id
    assert restored.symbol == original.symbol
    assert restored.direction == original.direction
    assert restored.recommendation == original.recommendation
    assert restored.entry == original.entry
    assert restored.stop == original.stop
    assert restored.status is original.status
    assert restored.created_candle_ts == original.created_candle_ts


def test_round_trip_preserves_rich_context():
    original = _record()
    restored = PredictionRecord.from_row(original.to_row())
    assert restored.context == original.context
    assert restored.market_regime == "Strong Uptrend"
    assert restored.sector == "Energy"
    assert restored.volatility_bucket == "Normal"
    assert restored.similarity_score == 0.84


def test_round_trip_preserves_all_three_version_stamps():
    """Versions are independent — a model swap must not invalidate older records."""
    original = _record()
    restored = PredictionRecord.from_row(original.to_row())
    assert restored.prediction_model_version == original.prediction_model_version
    assert restored.outcome_model_version == original.outcome_model_version
    assert restored.feature_version == original.feature_version


def test_from_row_tolerates_missing_columns():
    """Forward compatibility: older readers must survive newer/partial rows."""
    minimal = {
        "prediction_id": "abc",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_candle_ts": 1,
        "symbol": "TCS.NS",
        "exchange": "yahoo",
        "timeframe": "1d",
        "current_price": 100.0,
        "direction": "WAIT",
        "recommendation": "WAIT",
        "status": "PENDING",
    }
    restored = PredictionRecord.from_row(minimal)
    assert restored.symbol == "TCS.NS"
    assert restored.context == {}
    assert restored.entry is None
    assert restored.feature_version is None


def test_from_row_survives_corrupt_context_json():
    row = _record().to_row()
    row["context_json"] = "{not valid json"
    assert PredictionRecord.from_row(row).context == {}


def test_empty_context_serialises_to_null():
    assert _record(context={}).to_row()["context_json"] is None


def test_touch_updates_the_timestamp():
    record = _record()
    record.updated_at = "2000-01-01T00:00:00+00:00"
    record.touch()
    assert record.updated_at != "2000-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Migrations & schema
# --------------------------------------------------------------------------- #

def test_migrations_create_the_expected_tables(conn):
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"predictions", "schema_migrations"} <= tables


def test_migration_records_its_version(conn):
    assert applied_versions(conn) == {m.version for m in MIGRATIONS}


def test_migrations_are_idempotent(db_path):
    """Running again applies nothing — safe to call on every startup."""
    first = initialize_database(db_path)
    assert run_migrations(first) == []          # already current
    first.close()

    second = get_connection(db_path)            # simulate a restart
    assert run_migrations(second) == []
    assert applied_versions(second) == {m.version for m in MIGRATIONS}
    second.close()


def test_first_run_applies_migration_one(db_path):
    conn = get_connection(db_path)
    assert run_migrations(conn) == [1]
    conn.close()


def test_schema_has_all_required_columns(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(predictions)")}
    required = {
        "prediction_id", "created_at", "updated_at", "created_candle_ts",
        "symbol", "exchange", "timeframe", "source", "current_price",
        "direction", "direction_prob", "outcome_prob", "decision_score",
        "recommendation", "entry", "stop", "target1", "target2",
        "market_regime", "market_phase", "sector", "session",
        "volatility_bucket", "similarity_score", "context_json",
        "prediction_model_version", "outcome_model_version", "feature_version",
        "status", "resolved_at", "resolved_price", "resolution_reason",
        "realised_r", "holding_bars",
    }
    assert required <= columns


def test_expected_indexes_exist(conn):
    indexes = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert {"idx_pred_once", "idx_pred_status", "idx_pred_symbol_created"} <= indexes


def _insert(conn: sqlite3.Connection, record: PredictionRecord) -> None:
    """Minimal raw insert — the real store arrives in Milestone 2."""
    row = record.to_row()
    conn.execute(
        f"INSERT INTO predictions ({', '.join(row)}) "
        f"VALUES ({', '.join(':' + k for k in row)})",
        row,
    )
    conn.commit()


def test_unique_index_blocks_duplicate_predictions(conn):
    """Duplicate protection for the monitor: one auto prediction per bar per market."""
    _insert(conn, _record())
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, _record())                # same symbol/timeframe/candle/source


def test_unique_index_allows_a_different_candle(conn):
    _insert(conn, _record())
    _insert(conn, _record(created_candle_ts=1_700_086_400))
    assert conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"] == 2


def test_unique_index_allows_a_different_source(conn):
    """A manual record may coexist with the automatic one for the same bar."""
    _insert(conn, _record())
    _insert(conn, _record(source="manual"))
    assert conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"] == 2


def test_record_survives_a_database_round_trip(conn):
    original = _record()
    _insert(conn, original)
    row = conn.execute(
        "SELECT * FROM predictions WHERE prediction_id = ?", (original.prediction_id,)
    ).fetchone()
    restored = PredictionRecord.from_row(row)
    assert restored.symbol == original.symbol
    assert restored.context == original.context
    assert restored.feature_version == original.feature_version
    assert restored.status is PredictionStatus.PENDING


# --------------------------------------------------------------------------- #
# Connection configuration
# --------------------------------------------------------------------------- #

def test_connection_uses_wal_and_row_access(db_path):
    """WAL keeps the (later) monitor's writes from blocking API reads."""
    conn = get_connection(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    assert conn.row_factory is sqlite3.Row
    conn.close()


def test_connection_creates_the_parent_directory(tmp_path):
    nested = str(tmp_path / "nested" / "dir" / "prediction_history.db")
    conn = get_connection(nested)
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    conn.close()
