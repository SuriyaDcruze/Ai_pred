"""API integration tests for the Historical Memory router (Sprint 2 · Milestone 5).

Mount only the ``/memory/*`` router over temporary stores — production data is never touched.
Cover every endpoint, validation, error mapping, pagination, filters, the similarity-
unavailable contract, the build/backfill/rebuild operations, OpenAPI generation, and that the
handlers hold no business logic (no engine imports).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router as memory_router
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.builder import MemoryBuilder
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore

_TS = 1_700_000_000


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
def client(predictions, memory):
    app = FastAPI()
    app.state.forward_store = predictions
    app.state.memory_store = memory
    app.state.retrieval = RetrievalEngine(predictions, memory)
    app.state.memory_builder = MemoryBuilder(predictions, memory)
    app.include_router(memory_router)
    with TestClient(app) as c:
        yield c


def _seed(store, *, i=0, symbol="REL.NS", sector="Energy", regime="BULL", timeframe="1d",
          pmv="pred-1", prob=0.62, r=2.0, created=None):
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe=timeframe, current_price=100.0,
        direction="BUY", recommendation="BUY", created_candle_ts=_TS + i,
        entry=100.0, stop=95.0, target1=110.0, outcome_prob=prob,
        sector=sector, market_regime=regime, prediction_model_version=pmv,
        status=PredictionStatus.ACTIVE,
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


# --------------------------------------------------------------------------- empty state
def test_empty_endpoints(client):
    assert client.get("/memory/search").json() == {"count": 0, "next_cursor": None, "records": []}
    assert client.get("/memory/statistics").json()["total_resolved"] == 0
    assert client.get("/memory/context").json()["sample_size"] == 0
    assert client.get("/memory/timeline").json()["count"] == 0


# --------------------------------------------------------------------------- record
def test_record_composed(client, predictions):
    pid = _seed(predictions)
    client.post(f"/memory/build/{pid}")
    rec = client.get(f"/memory/record/{pid}").json()["record"]
    assert rec["prediction_id"] == pid and rec["trade_result"] == "WIN"
    assert rec["reasoning"] is not None and rec["metadata"]["built"] is True


def test_record_unknown_404(client):
    assert client.get("/memory/record/nope").status_code == 404


# --------------------------------------------------------------------------- build
def test_build_is_idempotent(client, predictions):
    pid = _seed(predictions)
    first = client.post(f"/memory/build/{pid}")
    assert first.status_code == 200 and first.json()["status"] == "built"
    assert client.post(f"/memory/build/{pid}").json()["status"] == "built"   # idempotent


def test_build_unknown_404(client):
    assert client.post("/memory/build/nope").status_code == 404


def test_build_open_prediction_reports_skipped(client, predictions):
    pid = _seed(predictions, r=None)   # still ACTIVE
    body = client.post(f"/memory/build/{pid}")
    assert body.status_code == 200 and body.json()["status"] == "skipped_open"


# --------------------------------------------------------------------------- search + filters
def test_search_filters_combine(client, predictions):
    _seed(predictions, i=0, symbol="REL.NS", sector="Energy", prob=0.75, created="2026-01-01T00:00:00+00:00")
    _seed(predictions, i=1, symbol="TCS.NS", sector="IT", regime="BEAR", prob=0.55, created="2026-01-02T00:00:00+00:00")
    client.post("/memory/backfill")

    assert client.get("/memory/search", params={"symbol": "REL.NS"}).json()["count"] == 1
    assert client.get("/memory/search", params={"sector": "IT", "outcome": "WIN"}).json()["count"] == 1
    assert client.get("/memory/search", params={"confidence_min": 0.7}).json()["count"] == 1
    assert client.get("/memory/search", params={"market_regime": "BEAR"}).json()["count"] == 1


def test_search_invalid_filter_422(client):
    assert client.get("/memory/search", params={"confidence_min": 0.9, "confidence_max": 0.1}).status_code == 422
    assert client.get("/memory/search", params={"outcome": "NONSENSE"}).status_code == 422


def test_search_pagination(client, predictions):
    for i in range(10):
        _seed(predictions, i=i, symbol=f"S{i:02d}.NS", created=f"2026-01-01T00:00:{i:02d}+00:00")
    client.post("/memory/backfill")

    first = client.get("/memory/search", params={"limit": 4}).json()
    assert first["count"] == 4 and first["next_cursor"] is not None
    second = client.get("/memory/search", params={"limit": 4, "cursor": first["next_cursor"]}).json()
    ids = {r["prediction_id"] for r in first["records"]} | {r["prediction_id"] for r in second["records"]}
    assert len(ids) == 8   # no overlap between pages


def test_search_bad_cursor_422(client):
    assert client.get("/memory/search", params={"cursor": "!!!bad!!!"}).status_code == 422


def test_search_bad_limit_422(client):
    assert client.get("/memory/search", params={"limit": 0}).status_code == 422
    assert client.get("/memory/search", params={"limit": 99999}).status_code == 422


# --------------------------------------------------------------------------- statistics
def test_statistics_includes_sample_size(client, predictions):
    _seed(predictions, i=0, sector="Energy", r=2.0)
    _seed(predictions, i=1, sector="Energy", r=-1.0)
    client.post("/memory/backfill")

    stats = client.get("/memory/statistics", params={"dimension": "sector"}).json()
    assert stats["total_resolved"] == 2      # combined rollup, not double-counted
    energy = next(a for a in stats["aggregates"] if a["bucket"] == "Energy" and a["model_version"] == "")
    assert energy["n_resolved"] == 2 and energy["win_rate"] == 0.5


def test_statistics_unknown_dimension_422(client):
    assert client.get("/memory/statistics", params={"dimension": "bogus"}).status_code == 422


def test_statistics_bucket_without_dimension_422(client):
    assert client.get("/memory/statistics", params={"bucket": "Energy"}).status_code == 422


# --------------------------------------------------------------------------- timeline
def test_timeline_by_symbol_and_dates(client, predictions):
    _seed(predictions, i=0, symbol="REL.NS", created="2026-01-01T00:00:00+00:00")
    _seed(predictions, i=1, symbol="REL.NS", created="2026-02-01T00:00:00+00:00")
    client.post("/memory/backfill")
    body = client.get("/memory/timeline", params={"symbol": "REL.NS", "from": "2026-01-15T00:00:00+00:00"}).json()
    assert body["count"] == 1


# --------------------------------------------------------------------------- similarity
def test_similar_is_unavailable(client, predictions):
    pid = _seed(predictions)
    body = client.get(f"/memory/similar/{pid}").json()
    assert body == {"available": False, "reason": "Similarity Engine unavailable", "results": []}


def test_similar_unknown_404(client):
    assert client.get("/memory/similar/nope").status_code == 404


# --------------------------------------------------------------------------- context
def test_context_bundle(client, predictions):
    _seed(predictions, symbol="REL.NS")
    client.post("/memory/backfill")
    bundle = client.get("/memory/context", params={"symbol": "REL.NS", "k": 3}).json()
    assert bundle["metadata"]["k"] == 3 and bundle["metadata"]["symbol"] == "REL.NS"
    assert "sample_size" in bundle and "records" in bundle


# --------------------------------------------------------------------------- backfill / rebuild
def test_backfill_reports_counts(client, predictions):
    for i in range(3):
        _seed(predictions, i=i, symbol=f"S{i}.NS")
    body = client.post("/memory/backfill").json()
    assert body == {"processed": 3, "built": 3, "skipped": 0, "failed": 0}
    again = client.post("/memory/backfill").json()
    assert again["built"] == 0 and again["skipped"] == 3   # idempotent


def test_rebuild_aggregates(client, predictions):
    _seed(predictions, sector="Energy")
    client.post("/memory/backfill")
    body = client.post("/memory/rebuild-aggregates").json()
    assert body["rebuilt_rows"] > 0


# --------------------------------------------------------------------------- meta
def test_openapi_generates_with_all_memory_paths():
    app = FastAPI()
    app.include_router(memory_router)
    paths = app.openapi()["paths"]
    for expected in (
        "/memory/record/{prediction_id}", "/memory/search", "/memory/statistics",
        "/memory/timeline", "/memory/similar/{prediction_id}", "/memory/context",
        "/memory/build/{prediction_id}", "/memory/backfill", "/memory/rebuild-aggregates",
    ):
        assert expected in paths


def test_router_mounts_in_real_app():
    from app.api.main import app
    paths = {r.path for r in app.routes}
    assert "/memory/search" in paths and "/memory/build/{prediction_id}" in paths


def test_router_does_not_import_engines():
    import ast

    import app.api.memory as mem
    with open(mem.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
