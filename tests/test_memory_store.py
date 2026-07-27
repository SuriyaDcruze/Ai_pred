"""Unit tests for the Memory Store (Sprint 2 · Milestone 2).

Cover CRUD, idempotent upserts, thread safety / concurrent writes, transaction rollback,
duplicate protection, invalid foreign keys, and schema validation — over a temporary
database. The store must write **only** the satellite tables and never touch ``predictions``.
"""

from __future__ import annotations

import threading

import pytest

from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.errors import (
    MemoryConflictError,
    MemoryForeignKeyError,
    MemoryNotFoundError,
    MemorySchemaError,
)
from app.memory.models import (
    AggregateDimension,
    MemoryAggregate,
    MemoryEmbedding,
    MemoryReasoning,
)
from app.memory.store import MemoryStore


# --------------------------------------------------------------------------- fixtures
@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "prediction_history.db")


@pytest.fixture()
def predictions(db_path):
    """A PredictionStore seeded with a few predictions (FK parents for satellites)."""
    store = PredictionStore(path=db_path)
    ids = []
    for i in range(3):
        rec = PredictionRecord(
            symbol=f"S{i}.NS", exchange="NSE", timeframe="1d", current_price=100.0,
            direction="BUY", recommendation="BUY", created_candle_ts=1_700_000_000 + i,
            entry=100.0, stop=95.0, target1=110.0, sector="Energy",
            status=PredictionStatus.ACTIVE,
        )
        store.create(rec)
        ids.append(rec.prediction_id)
    try:
        yield store, ids
    finally:
        store.close()


@pytest.fixture()
def store(db_path, predictions):
    s = MemoryStore(path=db_path)
    try:
        yield s
    finally:
        s.close()


def _pid(predictions):
    return predictions[1][0]


# ------------------------------------------------------------------- reasoning CRUD
def test_reasoning_create_get_exists(store, predictions):
    pid = _pid(predictions)
    assert store.reasoning_exists(pid) is False
    store.create_reasoning(MemoryReasoning(pid, confidence=0.62, rationale="momentum", factors={"trend": "up"}))
    assert store.reasoning_exists(pid) is True
    got = store.get_reasoning(pid)
    assert got.confidence == 0.62 and got.factors == {"trend": "up"}


def test_reasoning_get_missing_returns_none(store, predictions):
    assert store.get_reasoning(_pid(predictions)) is None


def test_reasoning_create_duplicate_raises_conflict(store, predictions):
    pid = _pid(predictions)
    store.create_reasoning(MemoryReasoning(pid, confidence=0.5))
    with pytest.raises(MemoryConflictError):
        store.create_reasoning(MemoryReasoning(pid, confidence=0.9))


def test_reasoning_update_changes_fields(store, predictions):
    pid = _pid(predictions)
    store.create_reasoning(MemoryReasoning(pid, confidence=0.5, rationale="a"))
    store.update_reasoning(MemoryReasoning(pid, confidence=0.7, rationale="b"))
    got = store.get_reasoning(pid)
    assert got.confidence == 0.7 and got.rationale == "b"


def test_reasoning_update_missing_raises_not_found(store, predictions):
    with pytest.raises(MemoryNotFoundError):
        store.update_reasoning(MemoryReasoning(_pid(predictions), confidence=0.5))


def test_reasoning_delete(store, predictions):
    pid = _pid(predictions)
    store.create_reasoning(MemoryReasoning(pid, confidence=0.5))
    assert store.delete_reasoning(pid) is True
    assert store.reasoning_exists(pid) is False
    assert store.delete_reasoning(pid) is False   # already gone


def test_reasoning_upsert_is_idempotent(store, predictions):
    pid = _pid(predictions)
    store.upsert_reasoning(MemoryReasoning(pid, confidence=0.5))
    store.upsert_reasoning(MemoryReasoning(pid, confidence=0.8, rationale="revised"))
    # exactly one row, reflecting the latest write.
    assert store._fetchone("SELECT COUNT(*) AS n FROM memory_reasoning WHERE prediction_id = ?", (pid,))["n"] == 1
    got = store.get_reasoning(pid)
    assert got.confidence == 0.8 and got.rationale == "revised"


def test_reasoning_upsert_preserves_created_at(store, predictions):
    pid = _pid(predictions)
    first = store.upsert_reasoning(MemoryReasoning(pid, confidence=0.5))
    store.upsert_reasoning(MemoryReasoning(pid, confidence=0.9))
    assert store.get_reasoning(pid).created_at == first.created_at


# --------------------------------------------------------------- reasoning errors
def test_reasoning_foreign_key_violation(store):
    with pytest.raises(MemoryForeignKeyError):
        store.create_reasoning(MemoryReasoning("no-such-prediction", confidence=0.5))


def test_reasoning_invalid_schema_version(store, predictions):
    bad = MemoryReasoning(_pid(predictions), confidence=0.5)
    bad.schema_version = 999
    with pytest.raises(MemorySchemaError):
        store.create_reasoning(bad)


def test_foreign_key_violation_writes_nothing(store):
    with pytest.raises(MemoryForeignKeyError):
        store.create_reasoning(MemoryReasoning("ghost", confidence=0.5))
    assert store._fetchone("SELECT COUNT(*) AS n FROM memory_reasoning", ())["n"] == 0


# ------------------------------------------------------------------ embeddings CRUD
def test_embedding_create_get_list(store, predictions):
    pid = _pid(predictions)
    store.create_embedding(MemoryEmbedding(pid, embedding_kind="context_v1", model_name="e5", vector=[0.1, 0.2]))
    got = store.get_embedding(pid, "context_v1")
    assert got.model_name == "e5" and got.dim == 2
    assert got.vector == pytest.approx([0.1, 0.2], abs=1e-6)
    assert len(store.list_embeddings(pid)) == 1


def test_embedding_multiple_kinds_per_prediction(store, predictions):
    pid = _pid(predictions)
    store.create_embedding(MemoryEmbedding(pid, embedding_kind="context_v1"))
    store.create_embedding(MemoryEmbedding(pid, embedding_kind="context_v2"))
    kinds = {e.embedding_kind for e in store.list_embeddings(pid)}
    assert kinds == {"context_v1", "context_v2"}


def test_embedding_duplicate_kind_raises_conflict(store, predictions):
    pid = _pid(predictions)
    store.create_embedding(MemoryEmbedding(pid, embedding_kind="context_v1"))
    with pytest.raises(MemoryConflictError):
        store.create_embedding(MemoryEmbedding(pid, embedding_kind="context_v1"))


def test_embedding_upsert_is_idempotent(store, predictions):
    pid = _pid(predictions)
    store.upsert_embedding(MemoryEmbedding(pid, embedding_kind="context_v1", vector=[0.1]))
    store.upsert_embedding(MemoryEmbedding(pid, embedding_kind="context_v1", vector=[0.9, 0.8]))
    assert len(store.list_embeddings(pid)) == 1
    assert store.get_embedding(pid, "context_v1").vector == pytest.approx([0.9, 0.8], abs=1e-6)


def test_embedding_update_missing_raises_not_found(store, predictions):
    with pytest.raises(MemoryNotFoundError):
        store.update_embedding(MemoryEmbedding(_pid(predictions), embedding_kind="context_v1", vector=[0.1]))


def test_embedding_exists_and_empty_slot(store, predictions):
    pid = _pid(predictions)
    assert store.embedding_exists(pid, "context_v1") is False
    store.create_embedding(MemoryEmbedding(pid, embedding_kind="context_v1"))   # no vector yet
    assert store.embedding_exists(pid, "context_v1") is True
    assert store.get_embedding(pid, "context_v1").vector is None


def test_embedding_foreign_key_violation(store):
    with pytest.raises(MemoryForeignKeyError):
        store.create_embedding(MemoryEmbedding("ghost", embedding_kind="context_v1"))


# ------------------------------------------------------------------ aggregates CRUD
def _agg(bucket="Energy", model="pred-1", **kw):
    base = dict(n_resolved=5, wins=3, losses=2, win_rate=0.6, avg_r=0.4, expectancy=0.4,
                total_r=2.0, profit_factor=1.8, max_drawdown_r=1.1, avg_holding_bars=6.0)
    base.update(kw)
    return MemoryAggregate(dimension=AggregateDimension.SECTOR, bucket=bucket, model_version=model, **base)


def test_aggregate_upsert_get(store):
    store.upsert_aggregate(_agg())
    got = store.get_aggregate(AggregateDimension.SECTOR, "Energy", "pred-1")
    assert got.win_rate == 0.6 and got.n_resolved == 5


def test_aggregate_upsert_updates_in_place(store):
    store.upsert_aggregate(_agg(n_resolved=5, win_rate=0.6))
    store.upsert_aggregate(_agg(n_resolved=8, win_rate=0.75))
    got = store.get_aggregate(AggregateDimension.SECTOR, "Energy", "pred-1")
    assert got.n_resolved == 8 and got.win_rate == 0.75
    assert len(store.list_aggregates()) == 1


def test_aggregate_list_and_filter(store):
    store.upsert_aggregate(_agg(bucket="Energy"))
    store.upsert_aggregate(_agg(bucket="IT"))
    store.upsert_aggregate(MemoryAggregate(dimension=AggregateDimension.OVERALL, bucket="all"))
    assert len(store.list_aggregates()) == 3
    assert len(store.list_aggregates(AggregateDimension.SECTOR)) == 2


def test_aggregate_exists_and_delete(store):
    store.upsert_aggregate(_agg())
    assert store.aggregate_exists(AggregateDimension.SECTOR, "Energy", "pred-1") is True
    assert store.delete_aggregate(AggregateDimension.SECTOR, "Energy", "pred-1") is True
    assert store.aggregate_exists(AggregateDimension.SECTOR, "Energy", "pred-1") is False


def test_aggregate_model_version_kept_separate(store):
    store.upsert_aggregate(_agg(model="pred-1", win_rate=0.6))
    store.upsert_aggregate(_agg(model="pred-2", win_rate=0.4))
    assert store.get_aggregate(AggregateDimension.SECTOR, "Energy", "pred-1").win_rate == 0.6
    assert store.get_aggregate(AggregateDimension.SECTOR, "Energy", "pred-2").win_rate == 0.4


# ------------------------------------------------------------------ isolation
def test_store_never_writes_predictions(store, predictions):
    ps, ids = predictions
    pid = ids[0]
    before = dict(store._conn.execute("SELECT * FROM predictions WHERE prediction_id = ?", (pid,)).fetchone())
    count_before = store._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]

    store.upsert_reasoning(MemoryReasoning(pid, confidence=0.6))
    store.upsert_embedding(MemoryEmbedding(pid, vector=[0.1]))
    store.upsert_aggregate(_agg())

    after = dict(store._conn.execute("SELECT * FROM predictions WHERE prediction_id = ?", (pid,)).fetchone())
    assert after == before
    assert store._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"] == count_before


def test_store_does_not_import_engines():
    import ast

    import app.memory.store as ms
    with open(ms.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)


# ------------------------------------------------------------------ thread safety
def test_concurrent_upserts_distinct_predictions(store, predictions):
    _, ids = predictions
    errors: list[Exception] = []

    def work(pid: str, seed: int) -> None:
        try:
            for _ in range(20):
                store.upsert_reasoning(MemoryReasoning(pid, confidence=0.5))
                store.upsert_embedding(MemoryEmbedding(pid, vector=[float(seed)]))
        except Exception as exc:  # noqa: BLE001 - captured and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(pid, i)) for i, pid in enumerate(ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for pid in ids:
        assert store.reasoning_exists(pid) and store.embedding_exists(pid)
    # one reasoning row per prediction — no duplication under concurrency.
    assert store._fetchone("SELECT COUNT(*) AS n FROM memory_reasoning", ())["n"] == len(ids)


def test_concurrent_upserts_same_prediction_converge(store, predictions):
    pid = _pid(predictions)
    errors: list[Exception] = []

    def work(seed: int) -> None:
        try:
            for _ in range(30):
                store.upsert_reasoning(MemoryReasoning(pid, confidence=0.5 + seed * 0.01))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Still exactly one row despite heavy concurrent upserts on the same key.
    assert store._fetchone("SELECT COUNT(*) AS n FROM memory_reasoning WHERE prediction_id = ?", (pid,))["n"] == 1
