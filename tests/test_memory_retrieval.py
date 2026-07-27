"""Integration tests for the Retrieval Engine (Sprint 2 · Milestone 4).

Cover Memory Record composition (incl. missing satellites), every supported filter and their
combination, deterministic keyset pagination, aggregate reads, the similarity-unavailable
contract, the GPT context bundle, empty and large datasets, invalid inputs, and that
retrieval performs no writes (``predictions`` unchanged). Temporary databases only.
"""

from __future__ import annotations

import pytest

from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.builder import MemoryBuilder
from app.memory.errors import MemoryNotFoundError, MemoryQueryError
from app.memory.models import AggregateDimension
from app.memory.retrieval import MemoryFilter, RetrievalEngine
from app.memory.store import MemoryStore

_TS = 1_700_000_000


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


@pytest.fixture()
def engine(predictions, memory):
    return RetrievalEngine(predictions, memory)


def _seed(store, *, i=0, symbol="A.NS", timeframe="1d", regime="BULL", sector="Energy",
          pmv="pred-1", omv="out-1", fv="feat-1", prob=0.6, r=2.0, created=None):
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe=timeframe, current_price=100.0,
        direction="BUY", recommendation="BUY", created_candle_ts=_TS + i,
        entry=100.0, stop=95.0, target1=110.0, outcome_prob=prob,
        sector=sector, market_regime=regime, prediction_model_version=pmv,
        outcome_model_version=omv, feature_version=fv, status=PredictionStatus.ACTIVE,
    )
    if created is not None:
        rec.created_at = created
    store.create(rec)
    if r is not None:
        store.update_resolution(
            rec.prediction_id,
            status=PredictionStatus.TARGET_HIT if r > 0 else PredictionStatus.STOP_HIT,
            resolved_price=110.0 if r > 0 else 95.0,
            resolution_reason="target hit" if r > 0 else "stop hit",
            realised_r=r, holding_bars=5,
        )
    return rec.prediction_id


# ------------------------------------------------------------------- composition
def test_get_record_composes_prediction_and_satellites(engine, predictions, builder):
    pid = _seed(predictions, prob=0.62, r=2.0)
    builder.build(pid)
    rec = engine.get_record(pid).to_dict()

    assert rec["prediction_id"] == pid
    assert rec["trade_result"] == "WIN" and rec["realised_r"] == 2.0
    assert rec["confidence"] == 0.62
    assert rec["reasoning"] is not None and rec["reasoning"]["rationale"]
    assert rec["embedding"] is not None and rec["embedding"]["present"] is False  # placeholder
    assert rec["aggregate"] is not None and rec["aggregate"]["n_resolved"] >= 1
    assert rec["metadata"]["built"] is True and rec["metadata"]["builder_version"] == "1"


def test_get_record_without_memory_returns_defaults(engine, predictions):
    pid = _seed(predictions, r=2.0)   # resolved but NOT built
    rec = engine.get_record(pid).to_dict()
    assert rec["reasoning"] is None            # missing satellite → null, not an error
    assert rec["embedding"] is None
    assert rec["metadata"]["built"] is False
    assert rec["trade_result"] == "WIN"        # prediction fields still surfaced


def test_get_record_unknown_raises(engine):
    with pytest.raises(MemoryNotFoundError):
        engine.get_record("no-such-id")


def test_open_prediction_composes_with_open_result(engine, predictions):
    pid = _seed(predictions, r=None)   # still ACTIVE
    rec = engine.get_record(pid).to_dict()
    assert rec["trade_result"] == "OPEN" and rec["realised_r"] is None
    assert rec["reasoning"] is None


# ------------------------------------------------------------------- filters
@pytest.fixture()
def diverse(predictions, builder):
    """A varied, fully-built corpus for filter tests."""
    ids = {
        "energy_bull": _seed(predictions, i=0, symbol="REL.NS", sector="Energy", regime="BULL",
                             timeframe="1d", prob=0.75, r=2.0, created="2026-01-01T00:00:00+00:00"),
        "it_bear": _seed(predictions, i=1, symbol="TCS.NS", sector="IT", regime="BEAR",
                         timeframe="1h", prob=0.55, r=-1.0, pmv="pred-2", fv="feat-2",
                         created="2026-01-02T00:00:00+00:00"),
        "energy_bull2": _seed(predictions, i=2, symbol="REL.NS", sector="Energy", regime="BULL",
                              timeframe="1d", prob=0.65, r=2.0, created="2026-01-03T00:00:00+00:00"),
    }
    builder.backfill()
    return ids


def test_filter_by_symbol(engine, diverse):
    page = engine.search(MemoryFilter(symbol="REL.NS"))
    assert {r.symbol for r in page.records} == {"REL.NS"}
    assert page.count == 2


def test_filter_by_timeframe_regime_sector(engine, diverse):
    assert engine.search(MemoryFilter(timeframe="1h")).count == 1
    assert engine.search(MemoryFilter(market_regime="BEAR")).count == 1
    assert engine.search(MemoryFilter(sector="Energy")).count == 2


def test_filter_by_versions(engine, diverse):
    assert engine.search(MemoryFilter(prediction_model_version="pred-2")).count == 1
    assert engine.search(MemoryFilter(feature_version="feat-2")).count == 1
    assert engine.search(MemoryFilter(outcome_model_version="out-1")).count == 3


def test_filter_by_confidence_range(engine, diverse):
    page = engine.search(MemoryFilter(confidence_min=0.6, confidence_max=0.7))
    assert page.count == 1
    assert page.records[0].to_dict()["confidence"] == pytest.approx(0.65)


def test_filter_by_outcome_status_alias(engine, diverse):
    assert engine.search(MemoryFilter(outcome="WIN")).count == 2    # WIN → TARGET_HIT
    assert engine.search(MemoryFilter(outcome="LOSS")).count == 1


def test_filter_by_date_range(engine, diverse):
    page = engine.search(MemoryFilter(date_from="2026-01-02T00:00:00+00:00",
                                      date_to="2026-01-02T23:59:59+00:00"))
    assert page.count == 1
    assert page.records[0].symbol == "TCS.NS"


def test_filters_combine(engine, diverse):
    page = engine.search(MemoryFilter(sector="Energy", confidence_min=0.7))
    assert page.count == 1 and page.records[0].to_dict()["confidence"] == pytest.approx(0.75)


def test_invalid_filters_raise(engine):
    with pytest.raises(MemoryQueryError):
        engine.search(MemoryFilter(confidence_min=0.8, confidence_max=0.2))
    with pytest.raises(MemoryQueryError):
        engine.search(MemoryFilter(outcome="NONSENSE"))
    with pytest.raises(MemoryQueryError):
        engine.search(MemoryFilter(date_from="2026-02", date_to="2026-01"))


# ------------------------------------------------------------------- pagination
def test_pagination_is_deterministic_and_complete(engine, predictions, builder):
    for i in range(120):
        _seed(predictions, i=i, symbol=f"S{i:03d}.NS", created=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00", r=1.0)
    builder.backfill()

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        page = engine.search(limit=25, cursor=cursor)
        seen.extend(r.prediction_id for r in page.records)
        pages += 1
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
        assert pages < 10        # guards against a cursor that never terminates

    assert len(seen) == 120 and len(set(seen)) == 120     # every record once, no dupes
    # Deterministic: a second identical walk yields the same first page order.
    again = engine.search(limit=25)
    assert [r.prediction_id for r in again.records] == seen[:25]


def test_bad_pagination_raises(engine):
    with pytest.raises(MemoryQueryError):
        engine.search(limit=0)
    with pytest.raises(MemoryQueryError):
        engine.search(limit=99999)
    with pytest.raises(MemoryQueryError):
        engine.search(cursor="!!!not-base64!!!")


# ------------------------------------------------------------------- aggregates
def test_aggregate_retrieval_is_read_only(engine, diverse, memory):
    all_aggs = engine.aggregates()
    assert len(all_aggs) > 0
    sector = engine.aggregates(AggregateDimension.SECTOR)
    assert all(a.dimension is AggregateDimension.SECTOR for a in sector)
    energy = engine.aggregates(AggregateDimension.SECTOR, "Energy", "")
    assert len(energy) == 1 and energy[0].n_resolved == 2


def test_aggregate_bucket_without_dimension_raises(engine):
    with pytest.raises(MemoryQueryError):
        engine.aggregates(bucket="Energy")


def test_aggregate_missing_bucket_returns_empty(engine):
    assert engine.aggregates(AggregateDimension.SECTOR, "Nonexistent", "") == []


# ------------------------------------------------------------------- similarity
def test_similarity_is_unavailable_no_fake_scores(engine, predictions):
    pid = _seed(predictions, r=1.0)
    result = engine.similar(pid)
    assert result.available is False
    assert result.reason == "Similarity Engine unavailable"
    assert result.results == []       # never a fabricated score


def test_similarity_unknown_prediction_raises(engine):
    with pytest.raises(MemoryNotFoundError):
        engine.similar("no-such-id")


# ------------------------------------------------------------------- gpt bundle
def test_gpt_context_is_bounded_and_honest(engine, diverse):
    bundle = engine.gpt_context(symbol="REL.NS", k=1)
    assert len(bundle["records"]) == 1            # bounded by k
    assert bundle["metadata"]["k"] == 1 and bundle["metadata"]["symbol"] == "REL.NS"
    assert "sample_size" in bundle
    assert "too small" in bundle["metadata"]["note"].lower()   # honest sample caveat


def test_gpt_context_deterministic(engine, diverse):
    a = engine.gpt_context(k=2)
    b = engine.gpt_context(k=2)
    assert [r["prediction_id"] for r in a["records"]] == [r["prediction_id"] for r in b["records"]]


# ------------------------------------------------------------------- empty + isolation
def test_empty_database(engine):
    assert engine.search().count == 0
    assert engine.aggregates() == []
    bundle = engine.gpt_context()
    assert bundle["records"] == [] and bundle["sample_size"] == 0
    with pytest.raises(MemoryNotFoundError):
        engine.get_record("anything")


def test_retrieval_performs_no_writes(engine, predictions, builder, memory):
    pid = _seed(predictions, r=2.0)
    builder.build(pid)
    before = [dict(r) for r in memory._conn.execute("SELECT * FROM predictions ORDER BY prediction_id")]
    reasoning_before = memory._conn.execute("SELECT COUNT(*) AS n FROM memory_reasoning").fetchone()["n"]

    engine.get_record(pid)
    engine.search(MemoryFilter(symbol=pid[:1]))
    engine.aggregates()
    engine.similar(pid)
    engine.gpt_context(symbol="A.NS")

    after = [dict(r) for r in memory._conn.execute("SELECT * FROM predictions ORDER BY prediction_id")]
    assert after == before
    assert memory._conn.execute("SELECT COUNT(*) AS n FROM memory_reasoning").fetchone()["n"] == reasoning_before


def test_retrieval_does_not_import_engines():
    import ast

    import app.memory.retrieval as ret
    with open(ret.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
