"""API tests for the Decision Intelligence REST layer (Sprint 5 · Milestone 5).

Mount the intelligence router over temporary stores (deps on ``app.state``; and, separately, not
available). Cover every endpoint, request validation, deterministic serialization, the health +
version endpoints, the error taxonomy (400/404/409/422/503), route ownership (static before
catch-all), OpenAPI generation, and no-engine-imports. Temporary databases only.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.intelligence import API_VERSION, SCHEMA_VERSION, router as intelligence_router
from app.decision_intelligence.models import DECISION_INTELLIGENCE_VERSION
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore

_TS = 1_700_000_000


@pytest.fixture()
def stores(tmp_path):
    path = str(tmp_path / "prediction_history.db")
    ps = PredictionStore(path=path)
    ms = MemoryStore(path=path)
    try:
        yield ps, ms
    finally:
        ps.close()
        ms.close()


@pytest.fixture()
def client(stores):
    ps, ms = stores
    app = FastAPI()
    app.state.forward_store = ps
    app.state.retrieval = RetrievalEngine(ps, ms)
    app.include_router(intelligence_router)
    with TestClient(app) as c:
        c._stores = (ps, ms)  # type: ignore[attr-defined]
        yield c


@pytest.fixture()
def client_unavailable():
    app = FastAPI()                                    # no deps on state
    app.include_router(intelligence_router)
    with TestClient(app) as c:
        yield c


def _seed(ps, *, i, symbol="RELIANCE.NS", sector="Energy", resolve_r=2.0):
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe="1d", current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0, target1=110.0,
        direction_prob=0.61, outcome_prob=0.62, decision_score=0.3, sector=sector,
        market_regime="BULL", prediction_model_version="pred-1", outcome_model_version="out-1",
        feature_version="feat-1", status=PredictionStatus.ACTIVE,
    )
    rec.created_at = f"2026-01-01T00:00:{i:02d}+00:00"
    ps.create(rec)
    if resolve_r is not None:
        ps.update_resolution(rec.prediction_id, status=PredictionStatus.TARGET_HIT,
                             resolved_price=110.0, resolution_reason="t", realised_r=resolve_r,
                             holding_bars=5)
    return rec.prediction_id


# --------------------------------------------------------------- health / version
def test_health_ready(client):
    body = client.get("/intelligence/health").json()
    assert body["ready"] is True and body["status"] == "ready"
    assert body["decision_intelligence_version"] == DECISION_INTELLIGENCE_VERSION
    assert body["dependencies"]["prediction_store"] and body["dependencies"]["retrieval"]


def test_health_unavailable(client_unavailable):
    body = client_unavailable.get("/intelligence/health").json()
    assert body["ready"] is False and body["status"] == "unavailable"


def test_version_endpoint(client):
    body = client.get("/intelligence/version").json()
    assert body == {"api_version": API_VERSION,
                    "decision_intelligence_version": DECISION_INTELLIGENCE_VERSION,
                    "schema_version": SCHEMA_VERSION}


def test_health_not_swallowed_by_catch_all(client):
    # '/intelligence/health' must hit health, not '/{prediction_id}'.
    assert client.get("/intelligence/health").status_code == 200
    assert client.get("/intelligence/version").status_code == 200


# --------------------------------------------------------------- by prediction / symbol
def test_intelligence_by_prediction(client):
    pid = _seed(client._stores[0], i=1)
    body = client.get(f"/intelligence/{pid}").json()
    assert set(body) == {"versions", "decision", "evidence", "explanation", "confidence",
                         "prioritisation", "checksums"}
    assert body["decision"]["prediction_id"] == pid
    assert body["decision"]["checksum"] == body["checksums"]["decision"]
    assert "score" in body["confidence"] and "score" in body["prioritisation"]
    assert body["versions"]["decision_intelligence_version"] == DECISION_INTELLIGENCE_VERSION


def test_intelligence_by_symbol_latest(client):
    ps = client._stores[0]
    _seed(ps, i=1, symbol="RELIANCE.NS")
    latest = _seed(ps, i=2, symbol="RELIANCE.NS")       # newer created_at
    body = client.get("/intelligence/symbol/RELIANCE.NS").json()
    assert body["decision"]["prediction_id"] == latest


def test_faithful_serialization_matches_checksums(client):
    pid = _seed(client._stores[0], i=1)
    body = client.get(f"/intelligence/{pid}").json()
    assert body["evidence"]["checksum"] == body["checksums"]["evidence"]
    assert body["confidence"]["checksum"] == body["checksums"]["confidence"]


# --------------------------------------------------------------- determinism
def test_deterministic_response(client):
    pid = _seed(client._stores[0], i=1)
    a = client.get(f"/intelligence/{pid}").json()
    b = client.get(f"/intelligence/{pid}").json()
    assert a == b                                       # no wall-clock ⇒ byte-identical


# --------------------------------------------------------------- error taxonomy
def test_unknown_prediction_is_404(client):
    assert client.get("/intelligence/deadbeefdeadbeef").status_code == 404


def test_unknown_symbol_is_404(client):
    assert client.get("/intelligence/symbol/NOSUCH.NS").status_code == 404


def test_invalid_prediction_id_is_400(client):
    assert client.get("/intelligence/bad!id").status_code == 400


def test_invalid_symbol_is_400(client):
    assert client.get("/intelligence/symbol/bad$sym").status_code == 400


def test_schema_version_mismatch_is_409(client):
    pid = _seed(client._stores[0], i=1)
    assert client.get(f"/intelligence/{pid}", params={"schema_version": "di-999"}).status_code == 409
    assert client.get(f"/intelligence/{pid}", params={"schema_version": SCHEMA_VERSION}).status_code == 200


def test_dependencies_unavailable_is_503(client_unavailable):
    assert client_unavailable.get("/intelligence/deadbeef").status_code == 503


# --------------------------------------------------------------- OpenAPI / isolation
def test_openapi_lists_intelligence_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/intelligence/health", "/intelligence/version",
                 "/intelligence/symbol/{symbol}", "/intelligence/{prediction_id}"):
        assert path in paths


def test_intelligence_api_imports_no_engine():
    import ast

    import app.api.intelligence as api
    with open(api.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
