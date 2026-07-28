"""Tests for the Behavioural Learning Dataset Builder (Sprint 4 · Vol 15 · Milestone 1).

Cover deterministic generation + checksum, versioning, ordering, filtering, empty/insufficient
corpus (`INSUFFICIENT_DATA`), record validation (malformed / incomplete / inconsistent /
version / corrupted), the append-only learning migration, concurrency, and that the builder
performs no writes and imports no engine. Temporary databases only.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.database.connection import get_connection
from app.database.migrations import applied_versions, run_migrations
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.retrieval import MemoryFilter, RetrievalEngine
from app.memory.store import MemoryStore
from app.learning.dataset import LearningDatasetBuilder
from app.learning.models import (
    DATASET_VERSION,
    LEARNING_VERSION,
    CorruptedMetadataError,
    IncompleteOutcomeError,
    InconsistentTimestampError,
    InvalidMemoryRecordError,
    LearningRecord,
    LearningRun,
    LearningStatus,
    UnsupportedVersionError,
)

_TS = 1_700_000_000


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "prediction_history.db")


@pytest.fixture()
def stores(db_path):
    ps = PredictionStore(path=db_path)
    ms = MemoryStore(path=db_path)
    try:
        yield ps, ms
    finally:
        ps.close()
        ms.close()


@pytest.fixture()
def retrieval(stores):
    ps, ms = stores
    return RetrievalEngine(ps, ms)


def _seed(ps, *, i, symbol="REL.NS", sector="Energy", regime="BULL", tf="1d",
          created=None, resolve_r=2.0):
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe=tf, current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0, target1=110.0,
        outcome_prob=0.62, sector=sector, market_regime=regime, prediction_model_version="pred-1",
        feature_version="feat-1", status=PredictionStatus.ACTIVE,
    )
    if created is not None:
        rec.created_at = created
    ps.create(rec)
    if resolve_r is not None:
        ps.update_resolution(
            rec.prediction_id,
            status=PredictionStatus.TARGET_HIT if resolve_r > 0 else PredictionStatus.STOP_HIT,
            resolved_price=110.0 if resolve_r > 0 else 95.0, resolution_reason="t",
            realised_r=resolve_r, holding_bars=5,
        )
    return rec.prediction_id


def _memory_dict(**overrides):
    """A minimal valid Memory Record dict (the RetrievalEngine.to_dict() contract)."""
    base = {
        "prediction_id": "abc", "status": "TARGET_HIT", "trade_result": "WIN",
        "realised_r": 2.0, "confidence": 0.6, "holding_bars": 5,
        "created_at": "2026-01-01T00:00:00+00:00", "resolved_at": "2026-01-02T00:00:00+00:00",
        "symbol": "REL.NS", "sector": "Energy", "timeframe": "1d", "market_regime": "BULL",
        "market_phase": None, "versions": {"prediction_model_version": "pred-1", "feature_version": "feat-1"},
        "embedding": {"kind": "context_v1", "present": False, "dim": None},
        "metadata": {"built": True, "record_schema_version": 1},
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------- determinism
def test_identical_corpus_identical_dataset(retrieval, stores):
    ps, _ = stores
    for i in range(4):
        _seed(ps, i=i, symbol=f"S{i}.NS", created=f"2026-01-01T00:00:0{i}+00:00")
    b = LearningDatasetBuilder(retrieval, min_corpus=1)
    a1 = b.build()
    a2 = b.build()
    assert a1.checksum == a2.checksum
    assert [r.prediction_id for r in a1.records] == [r.prediction_id for r in a2.records]


def test_checksum_reflects_content(retrieval, stores):
    ps, _ = stores
    _seed(ps, i=0, created="2026-01-01T00:00:00+00:00")
    b = LearningDatasetBuilder(retrieval, min_corpus=1)
    first = b.build().checksum
    _seed(ps, i=1, symbol="TCS.NS", created="2026-01-02T00:00:00+00:00")
    assert b.build().checksum != first          # a new record changes the fingerprint


def test_versions_and_metadata(retrieval, stores):
    ps, _ = stores
    _seed(ps, i=0)
    ds = LearningDatasetBuilder(retrieval, min_corpus=1).build()
    assert ds.dataset_version == DATASET_VERSION and ds.learning_version == LEARNING_VERSION
    assert ds.corpus_size == 1 and ds.checksum and ds.generated_at
    assert ds.source_versions["prediction_model_versions"] == ["pred-1"]


def test_deterministic_ordering(retrieval, stores):
    ps, _ = stores
    _seed(ps, i=2, symbol="C.NS", created="2026-01-03T00:00:00+00:00")
    _seed(ps, i=0, symbol="A.NS", created="2026-01-01T00:00:00+00:00")
    _seed(ps, i=1, symbol="B.NS", created="2026-01-02T00:00:00+00:00")
    ds = LearningDatasetBuilder(retrieval, min_corpus=1).build()
    ts = [r.prediction_timestamp for r in ds.records]
    assert ts == sorted(ts)


# --------------------------------------------------------------- filtering / rebuild
def test_filtered_build(retrieval, stores):
    ps, _ = stores
    _seed(ps, i=0, sector="Energy")
    _seed(ps, i=1, sector="IT", symbol="TCS.NS")
    ds = LearningDatasetBuilder(retrieval, min_corpus=1).build(filter=MemoryFilter(sector="IT"))
    assert ds.corpus_size == 1 and ds.records[0].sector == "IT"
    assert ds.filter == {"sector": "IT"}


def test_incremental_rebuild_is_stable(retrieval, stores):
    ps, _ = stores
    for i in range(3):
        _seed(ps, i=i, symbol=f"S{i}.NS")
    b = LearningDatasetBuilder(retrieval, min_corpus=1)
    assert b.build().checksum == b.build().checksum


def test_only_completed_trades_included(retrieval, stores):
    ps, _ = stores
    _seed(ps, i=0, resolve_r=2.0)                 # completed
    _seed(ps, i=1, resolve_r=None)                # open → excluded
    ds = LearningDatasetBuilder(retrieval, min_corpus=1).build()
    assert ds.corpus_size == 1 and ds.records[0].win is True


# --------------------------------------------------------------- empty / insufficient
def test_empty_corpus_is_insufficient(retrieval):
    ds = LearningDatasetBuilder(retrieval).build()
    assert ds.status is LearningStatus.INSUFFICIENT_DATA
    assert ds.corpus_size == 0 and ds.records == () and ds.is_sufficient is False


def test_below_min_corpus_is_insufficient_but_keeps_records(retrieval, stores):
    ps, _ = stores
    for i in range(3):
        _seed(ps, i=i, symbol=f"S{i}.NS")
    ds = LearningDatasetBuilder(retrieval, min_corpus=10).build()
    assert ds.status is LearningStatus.INSUFFICIENT_DATA and ds.corpus_size == 3


def test_at_or_above_min_corpus_is_sufficient(retrieval, stores):
    ps, _ = stores
    for i in range(5):
        _seed(ps, i=i, symbol=f"S{i}.NS")
    ds = LearningDatasetBuilder(retrieval, min_corpus=5).build()
    assert ds.status is None and ds.is_sufficient is True


# --------------------------------------------------------------- record validation
def test_from_memory_record_valid():
    r = LearningRecord.from_memory_record(_memory_dict(realised_r=-1.0, trade_result="LOSS"))
    assert r.win is False and r.outcome == "LOSS" and r.realised_r == -1.0
    assert r.memory_reference["prediction_id"] == "abc"


def test_malformed_record_rejected():
    with pytest.raises(InvalidMemoryRecordError):
        LearningRecord.from_memory_record(["not", "a", "record"])
    with pytest.raises(InvalidMemoryRecordError):
        LearningRecord.from_memory_record(_memory_dict(prediction_id=None))


def test_incomplete_outcome_rejected():
    with pytest.raises(IncompleteOutcomeError):
        LearningRecord.from_memory_record(_memory_dict(realised_r=None))
    with pytest.raises(IncompleteOutcomeError):
        LearningRecord.from_memory_record(_memory_dict(status=None))


def test_inconsistent_timestamps_rejected():
    with pytest.raises(InconsistentTimestampError):
        LearningRecord.from_memory_record(_memory_dict(
            created_at="2026-02-01T00:00:00+00:00", resolved_at="2026-01-01T00:00:00+00:00"))


def test_unsupported_version_rejected():
    bad = _memory_dict()
    bad["metadata"] = {"record_schema_version": 99}
    with pytest.raises(UnsupportedVersionError):
        LearningRecord.from_memory_record(bad)


def test_corrupted_metadata_rejected():
    with pytest.raises(CorruptedMetadataError):
        LearningRecord.from_memory_record(_memory_dict(metadata="not-a-dict"))


# --------------------------------------------------------------- migration (append-only)
def test_learning_runs_migration(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    assert 6 in applied_versions(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(learning_runs)")}
    assert {"run_id", "kind", "learning_version", "dataset_version", "corpus_size",
            "checksum", "status", "build_duration_ms"} <= cols


def test_learning_migration_leaves_predictions_unchanged(tmp_path):
    path = str(tmp_path / "ph.db")
    ps = PredictionStore(path=path)          # applies all migrations incl. 0006
    _seed(ps, i=0)
    schema = ps._conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()["sql"]
    run_migrations(ps._conn)                 # re-run: no-op
    assert ps._conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()["sql"] == schema
    assert ps.count() == 1
    ps.close()


def test_learning_run_row_round_trip(tmp_path):
    conn = get_connection(str(tmp_path / "ph.db"))
    run_migrations(conn)
    run = LearningRun(run_id="r1", kind="dataset", learning_version=LEARNING_VERSION,
                      dataset_version=DATASET_VERSION, corpus_size=5, checksum="abc", status="INSUFFICIENT_DATA")
    row = run.to_row()
    cols = ", ".join(row)
    with conn:
        conn.execute(f"INSERT INTO learning_runs ({cols}) VALUES ({', '.join(':' + k for k in row)})", row)
    got = LearningRun.from_row(conn.execute("SELECT * FROM learning_runs WHERE run_id='r1'").fetchone())
    assert got.corpus_size == 5 and got.status == "INSUFFICIENT_DATA" and got.kind == "dataset"


# --------------------------------------------------------------- concurrency / isolation
def test_concurrent_builds(retrieval, stores):
    ps, _ = stores
    for i in range(6):
        _seed(ps, i=i, symbol=f"S{i}.NS")
    builder = LearningDatasetBuilder(retrieval, min_corpus=1)
    checksums: list[str] = []
    errors: list[Exception] = []

    def work() -> None:
        try:
            checksums.append(builder.build().checksum)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(checksums)) == 1     # all builds agree


def test_build_performs_no_writes(retrieval, stores):
    ps, ms = stores
    for i in range(3):
        _seed(ps, i=i, symbol=f"S{i}.NS")
    preds = ms._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
    runs = ms._conn.execute("SELECT COUNT(*) AS n FROM learning_runs").fetchone()["n"]
    LearningDatasetBuilder(retrieval, min_corpus=1).build()
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"] == preds
    assert ms._conn.execute("SELECT COUNT(*) AS n FROM learning_runs").fetchone()["n"] == runs   # 0, builder writes nothing


def test_learning_modules_do_not_import_engines():
    import ast

    import app.learning.dataset as ds
    import app.learning.models as md
    for module in (ds, md):
        with open(module.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)


def test_learning_status_values():
    assert {s.value for s in LearningStatus} == {"VALIDATED", "HYPOTHESIS", "INSUFFICIENT_DATA"}
