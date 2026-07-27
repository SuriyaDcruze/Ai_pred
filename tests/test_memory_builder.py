"""Integration tests for the Memory Builder (Sprint 2 · Milestone 3).

Cover building one completed prediction, skipping open ones, aggregate maintenance +
correctness, duplicate builds, backfill idempotency, rollback/resilience, concurrent builds,
that the ``predictions`` table is never modified, and that no engine is imported. All tests
use temporary databases.
"""

from __future__ import annotations

import threading

import pytest

from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.aggregates import compute_aggregates
from app.memory.builder import BuildStatus, MemoryBuilder
from app.memory.models import AggregateDimension
from app.memory.store import MemoryStore


# --------------------------------------------------------------------------- fixtures
@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "prediction_history.db")


@pytest.fixture()
def predictions(db_path):
    store = PredictionStore(path=db_path)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture()
def memory(db_path, predictions):
    store = MemoryStore(path=db_path)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture()
def builder(predictions, memory):
    return MemoryBuilder(predictions, memory)


_TS = 1_700_000_000


def _make_prediction(
    store, *, symbol="RELIANCE.NS", sector="Energy", regime="BULL", timeframe="1d",
    model="pred-1", prob=0.62, ts_offset=0, resolve_r=None, terminal=True,
):
    """Create a prediction and optionally resolve it. Returns the prediction_id."""
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe=timeframe, current_price=100.0,
        direction="BUY", recommendation="BUY", created_candle_ts=_TS + ts_offset,
        entry=100.0, stop=95.0, target1=110.0, outcome_prob=prob,
        sector=sector, market_regime=regime, prediction_model_version=model,
        status=PredictionStatus.ACTIVE,
    )
    store.create(rec)
    if terminal and resolve_r is not None:
        store.update_resolution(
            rec.prediction_id,
            status=PredictionStatus.TARGET_HIT if resolve_r > 0 else PredictionStatus.STOP_HIT,
            resolved_price=110.0 if resolve_r > 0 else 95.0,
            resolution_reason="target hit" if resolve_r > 0 else "stop hit",
            realised_r=resolve_r, holding_bars=5,
        )
    return rec.prediction_id


# --------------------------------------------------------------------- build one
def test_build_completed_prediction(builder, memory, predictions):
    pid = _make_prediction(predictions, resolve_r=2.0)
    assert builder.build(pid) is BuildStatus.BUILT

    reasoning = memory.get_reasoning(pid)
    assert reasoning is not None
    assert reasoning.confidence == 0.62
    assert reasoning.factors["recommendation"] == "BUY"
    assert reasoning.factors["_builder"]["version"] == "1"      # build metadata present
    assert reasoning.factors["_builder"]["provenance"] == "memory_builder"
    assert "realised=+2.00R" in reasoning.rationale

    # embedding placeholder exists with NO vector (builder never computes embeddings).
    emb = memory.get_embedding(pid)
    assert emb is not None and emb.vector is None


def test_build_skips_open_prediction(builder, memory, predictions):
    pid = _make_prediction(predictions, resolve_r=None, terminal=False)  # still ACTIVE
    assert builder.build(pid) is BuildStatus.SKIPPED_OPEN
    assert memory.get_reasoning(pid) is None
    assert memory.get_embedding(pid) is None


def test_build_skips_missing_prediction(builder, memory):
    assert builder.build("no-such-id") is BuildStatus.SKIPPED_MISSING
    assert memory.get_reasoning("no-such-id") is None


def test_duplicate_build_is_idempotent(builder, memory, predictions):
    pid = _make_prediction(predictions, resolve_r=2.0)
    builder.build(pid)
    first = memory.get_reasoning(pid)
    builder.build(pid)   # again
    second = memory.get_reasoning(pid)

    # exactly one reasoning row, one embedding, identical content + preserved created_at.
    assert memory._fetchone("SELECT COUNT(*) AS n FROM memory_reasoning WHERE prediction_id=?", (pid,))["n"] == 1
    assert len(memory.list_embeddings(pid)) == 1
    assert second.created_at == first.created_at
    assert second.factors == first.factors and second.rationale == first.rationale


def test_rebuild_never_overwrites_a_populated_embedding_vector(builder, memory, predictions):
    from app.memory.models import MemoryEmbedding
    pid = _make_prediction(predictions, resolve_r=2.0)
    builder.build(pid)
    # Simulate the future Similarity Engine populating the vector.
    memory.upsert_embedding(MemoryEmbedding(pid, vector=[0.1, 0.2, 0.3]))
    builder.build(pid)   # rebuild must NOT null the vector
    assert memory.get_embedding(pid).vector == pytest.approx([0.1, 0.2, 0.3], abs=1e-6)


# ------------------------------------------------------------------ aggregates
def test_aggregates_reflect_builds_incrementally(builder, memory, predictions):
    _make_prediction(predictions, sector="Energy", ts_offset=0, resolve_r=2.0)
    builder.backfill()
    energy = memory.get_aggregate(AggregateDimension.SECTOR, "Energy", "")
    assert energy.n_resolved == 1 and energy.win_rate == 1.0

    _make_prediction(predictions, sector="Energy", ts_offset=1, resolve_r=-1.0)
    builder.backfill()
    energy = memory.get_aggregate(AggregateDimension.SECTOR, "Energy", "")
    assert energy.n_resolved == 2 and energy.win_rate == 0.5


def test_aggregate_correctness_against_known_dataset(builder, memory, predictions):
    # 3 Energy trades: +2R, +2R, -1R  → win_rate 2/3, avg_r 1.0, PF 4.0
    for i, r in enumerate([2.0, 2.0, -1.0]):
        _make_prediction(predictions, sector="Energy", ts_offset=i, resolve_r=r)
    builder.refresh_aggregates()

    agg = memory.get_aggregate(AggregateDimension.SECTOR, "Energy", "")
    assert agg.n_resolved == 3
    assert agg.wins == 2 and agg.losses == 1
    assert agg.win_rate == pytest.approx(2 / 3)
    assert agg.avg_r == pytest.approx(1.0) and agg.expectancy == pytest.approx(1.0)
    assert agg.total_r == pytest.approx(3.0)
    assert agg.profit_factor == pytest.approx(4.0)      # gross win 4 / gross loss 1

    overall = memory.get_aggregate(AggregateDimension.OVERALL, "all", "")
    assert overall.n_resolved == 3


def test_aggregate_per_model_version_not_blended(builder, memory, predictions):
    _make_prediction(predictions, sector="Energy", model="pred-1", ts_offset=0, resolve_r=2.0)
    _make_prediction(predictions, sector="Energy", model="pred-2", ts_offset=1, resolve_r=-1.0)
    builder.refresh_aggregates()

    combined = memory.get_aggregate(AggregateDimension.SECTOR, "Energy", "")
    assert combined.n_resolved == 2 and combined.win_rate == 0.5
    m1 = memory.get_aggregate(AggregateDimension.SECTOR, "Energy", "pred-1")
    m2 = memory.get_aggregate(AggregateDimension.SECTOR, "Energy", "pred-2")
    assert m1.win_rate == 1.0 and m2.win_rate == 0.0     # not blended


def test_cancelled_predictions_excluded_from_aggregates(builder, memory, predictions):
    pid = _make_prediction(predictions, resolve_r=2.0, ts_offset=0)
    # a terminal-but-unrealised (CANCELLED) prediction must not count in aggregates.
    rec = PredictionRecord(
        symbol="X.NS", exchange="NSE", timeframe="1d", current_price=1.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + 99, sector="Energy",
        prediction_model_version="pred-1", status=PredictionStatus.ACTIVE,
    )
    predictions.create(rec)
    predictions.update_status(rec.prediction_id, PredictionStatus.CANCELLED)
    builder.refresh_aggregates()
    assert memory.get_aggregate(AggregateDimension.SECTOR, "Energy", "").n_resolved == 1


def test_empty_state_produces_no_aggregates(builder, memory):
    assert builder.refresh_aggregates() == 0
    assert memory.list_aggregates() == []


# --------------------------------------------------------------------- backfill
def test_backfill_enriches_all_missing(builder, memory, predictions):
    ids = [_make_prediction(predictions, ts_offset=i, resolve_r=(1.0 if i % 2 else -1.0)) for i in range(5)]
    summary = builder.backfill()
    assert summary.scanned == 5 and summary.built == 5 and summary.skipped == 0 and summary.failed == 0
    for pid in ids:
        assert memory.reasoning_exists(pid)


def test_backfill_is_idempotent(builder, memory, predictions):
    for i in range(4):
        _make_prediction(predictions, ts_offset=i, resolve_r=1.0)
    first = builder.backfill()
    energy_first = memory.get_aggregate(AggregateDimension.OVERALL, "all", "")
    second = builder.backfill()
    energy_second = memory.get_aggregate(AggregateDimension.OVERALL, "all", "")

    assert first.built == 4
    assert second.built == 0 and second.skipped == 4     # nothing re-built
    assert energy_second.n_resolved == energy_first.n_resolved == 4  # same final state


def test_backfill_ignores_open_predictions(builder, memory, predictions):
    _make_prediction(predictions, ts_offset=0, resolve_r=1.0)          # terminal
    _make_prediction(predictions, ts_offset=1, terminal=False)          # open
    summary = builder.backfill()
    # list_completed only returns terminal predictions, so only one is scanned/built.
    assert summary.scanned == 1 and summary.built == 1


# --------------------------------------------------------------- rollback / errors
def test_backfill_survives_one_enrichment_failure(builder, memory, predictions, monkeypatch):
    from app.memory.errors import MemoryStoreError
    ids = [_make_prediction(predictions, ts_offset=i, resolve_r=1.0) for i in range(3)]

    calls = {"n": 0}
    real_upsert = memory.upsert_reasoning

    def flaky(reasoning):
        calls["n"] += 1
        if calls["n"] == 2:                      # fail the 2nd enrichment only
            raise MemoryStoreError("boom")
        return real_upsert(reasoning)

    monkeypatch.setattr(memory, "upsert_reasoning", flaky)
    summary = builder.backfill()
    assert summary.built == 2 and summary.failed == 1     # batch continued
    # The failed prediction simply has no memory — no corruption; a later run self-heals.
    built = [pid for pid in ids if memory.reasoning_exists(pid)]
    assert len(built) == 2


def test_hook_disabled_by_default(builder, predictions):
    pid = _make_prediction(predictions, resolve_r=1.0)
    assert builder.on_resolved(pid) is None            # no-op unless enabled


def test_hook_builds_when_enabled(predictions, memory):
    b = MemoryBuilder(predictions, memory, hook_enabled=True)
    pid = _make_prediction(predictions, resolve_r=1.0)
    assert b.on_resolved(pid) is BuildStatus.BUILT
    assert memory.reasoning_exists(pid)


# --------------------------------------------------------------- concurrency + isolation
def test_concurrent_builds(predictions, memory):
    ids = [_make_prediction(predictions, symbol=f"S{i}.NS", ts_offset=i, resolve_r=1.0) for i in range(6)]
    builder = MemoryBuilder(predictions, memory)
    errors: list[Exception] = []

    def work(pid: str) -> None:
        try:
            builder.build(pid, refresh_aggregates=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(pid,)) for pid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for pid in ids:
        assert memory.reasoning_exists(pid)
    builder.refresh_aggregates()
    assert memory.get_aggregate(AggregateDimension.OVERALL, "all", "").n_resolved == 6


def test_builder_never_modifies_predictions(builder, predictions, memory):
    pid = _make_prediction(predictions, resolve_r=2.0)
    before = dict(memory._conn.execute("SELECT * FROM predictions WHERE prediction_id=?", (pid,)).fetchone())
    count_before = memory._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]

    builder.build(pid)
    builder.backfill()

    after = dict(memory._conn.execute("SELECT * FROM predictions WHERE prediction_id=?", (pid,)).fetchone())
    assert after == before
    assert memory._conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"] == count_before


def test_builder_and_aggregates_do_not_import_engines():
    import ast

    import app.memory.aggregates as agg
    import app.memory.builder as bld
    for module in (bld, agg):
        with open(module.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)


def test_compute_aggregates_pure_on_empty():
    assert compute_aggregates([]) == []
