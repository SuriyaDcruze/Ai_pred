"""API tests for the Similarity REST layer (Sprint 3 · Vol 14 · Milestone 5).

Mount the similarity router over temporary stores with the engine injected (and, separately,
not injected). Cover every endpoint, validation, serialization, error mapping, empty corpus,
unavailable engine, determinism, OpenAPI generation, and that no raw vectors leak.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.similarity import router as similarity_router
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.memory.models import MemoryEmbedding
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore
from app.similarity.embedding import EMBEDDING_KIND, EMBEDDING_VERSION
from app.similarity.feature_vector import FEATURE_VERSION, VECTOR_DIM
from app.similarity.search import SIMILARITY_VERSION, SimilaritySearchEngine

_TS = 1_700_000_000


def _axis(idx: int, sign: float = 1.0) -> list[float]:
    v = [0.0] * VECTOR_DIM
    v[idx] = sign
    return v


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


def _seed(ps, ms, *, i, vector, sector="Energy", resolve_r=2.0, embed=True):
    rec = PredictionRecord(
        symbol=f"S{i:02d}.NS", exchange="NSE", timeframe="1d", current_price=100.0,
        direction="BUY", recommendation="BUY", created_candle_ts=_TS + i, entry=100.0,
        stop=95.0, target1=110.0, outcome_prob=0.6, sector=sector, market_regime="BULL",
        prediction_model_version="pred-1", status=PredictionStatus.ACTIVE,
    )
    ps.create(rec)
    if resolve_r is not None:
        ps.update_resolution(
            rec.prediction_id,
            status=PredictionStatus.TARGET_HIT if resolve_r > 0 else PredictionStatus.STOP_HIT,
            resolved_price=110.0 if resolve_r > 0 else 95.0, resolution_reason="t",
            realised_r=resolve_r, holding_bars=5,
        )
    if embed:
        ms.upsert_embedding(MemoryEmbedding(
            prediction_id=rec.prediction_id, embedding_kind=EMBEDDING_KIND,
            model_name=f"{EMBEDDING_VERSION}/{FEATURE_VERSION}", dim=VECTOR_DIM,
            vector=list(vector), schema_version=1,
        ))
    return rec.prediction_id


@pytest.fixture()
def client(stores):
    ps, ms = stores
    app = FastAPI()
    app.state.forward_store = ps
    retrieval = RetrievalEngine(ps, ms)
    engine = SimilaritySearchEngine(retrieval, ms)
    retrieval.set_similarity_engine(engine)
    app.state.retrieval = retrieval
    app.state.similarity_engine = engine
    app.include_router(similarity_router)
    with TestClient(app) as c:
        c._stores = (ps, ms)  # type: ignore[attr-defined]
        yield c


@pytest.fixture()
def client_disabled(stores):
    ps, ms = stores
    app = FastAPI()
    app.state.forward_store = ps
    app.state.retrieval = RetrievalEngine(ps, ms)   # no engine injected / on state
    app.include_router(similarity_router)
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------- health
def test_health_enabled(client):
    body = client.get("/memory/similar/health").json()
    assert body == {
        "enabled": True, "embedding_version": EMBEDDING_VERSION, "feature_version": FEATURE_VERSION,
        "vector_dimension": VECTOR_DIM, "search_version": SIMILARITY_VERSION,
    }


def test_health_disabled(client_disabled):
    body = client_disabled.get("/memory/similar/health").json()
    assert body["enabled"] is False and body["vector_dimension"] == VECTOR_DIM


def test_health_not_swallowed_by_id_route(client):
    # '/memory/similar/health' must hit the health handler, not '/{prediction_id}'.
    assert client.get("/memory/similar/health").status_code == 200


# --------------------------------------------------------------- by id
def test_similar_by_id(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0), resolve_r=2.0)
    _seed(ps, ms, i=2, vector=_axis(0), resolve_r=2.0)
    _seed(ps, ms, i=3, vector=_axis(1), resolve_r=-1.0)
    body = client.get(f"/memory/similar/{q}").json()

    assert body["available"] is True and body["prediction_id"] == q
    ids = [n["prediction_id"] for n in body["neighbours"]]
    assert q not in ids
    assert body["neighbours"][0]["similarity_score"] >= body["neighbours"][-1]["similarity_score"]
    assert body["sample_size"] == len(body["neighbours"])
    assert body["summary"]["win_rate"] is not None
    assert body["versions"]["similarity_version"] == SIMILARITY_VERSION
    assert body["versions"]["vector_dimension"] == VECTOR_DIM
    # never leaks raw vectors / internals
    n = body["neighbours"][0]
    assert "vector" not in n and "embedding" not in n
    assert set(n) >= {"prediction_id", "similarity_score", "outcome", "realised_r", "confidence", "holding_period"}


def test_similar_by_id_top_k(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    for i in range(2, 6):
        _seed(ps, ms, i=i, vector=_axis(0))
    body = client.get(f"/memory/similar/{q}", params={"top_k": 2}).json()
    assert len(body["neighbours"]) == 2


def test_similar_by_id_filter(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0), sector="Energy")
    _seed(ps, ms, i=2, vector=_axis(0), sector="Energy")
    _seed(ps, ms, i=3, vector=_axis(0), sector="IT")
    body = client.get(f"/memory/similar/{q}", params={"sector": "IT"}).json()
    assert all(n["sector"] == "IT" for n in body["neighbours"])


def test_similar_empty_corpus(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0))     # only the query is embedded
    body = client.get(f"/memory/similar/{q}").json()
    assert body["available"] is True and body["neighbours"] == [] and body["sample_size"] == 0


def test_similar_unknown_prediction_404(client):
    assert client.get("/memory/similar/nope").status_code == 404


def test_similar_missing_embedding_404(client):
    ps, ms = client._stores
    rec = PredictionRecord(
        symbol="X.NS", exchange="NSE", timeframe="1d", current_price=1.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + 99, status=PredictionStatus.ACTIVE,
    )
    ps.create(rec)   # exists but has no embedding
    assert client.get(f"/memory/similar/{rec.prediction_id}").status_code == 404


def test_similar_bad_params_400(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    assert client.get(f"/memory/similar/{q}", params={"top_k": 0}).status_code == 400
    assert client.get(f"/memory/similar/{q}", params={"threshold": 2.0}).status_code == 400


# --------------------------------------------------------------- collection GET
def test_similar_query_param_target(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    _seed(ps, ms, i=2, vector=_axis(0))
    body = client.get("/memory/similar", params={"prediction_id": q}).json()
    assert body["available"] is True and body["prediction_id"] == q


def test_similar_query_requires_prediction_id(client):
    assert client.get("/memory/similar").status_code == 400


# --------------------------------------------------------------- POST search
def test_post_search(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0), resolve_r=2.0)
    _seed(ps, ms, i=2, vector=_axis(0), resolve_r=2.0)
    body = client.post("/memory/similar/search", json={"prediction_id": q, "top_k": 5}).json()
    assert body["available"] is True and body["sample_size"] == len(body["neighbours"])


def test_post_search_bad_top_k_400(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    assert client.post("/memory/similar/search", json={"prediction_id": q, "top_k": 0}).status_code == 400


# --------------------------------------------------------------- unavailable engine (503)
def test_search_unavailable_returns_503(client_disabled):
    assert client_disabled.get("/memory/similar/anything").status_code == 503
    assert client_disabled.post("/memory/similar/search", json={"prediction_id": "x"}).status_code == 503
    assert client_disabled.get("/memory/similar", params={"prediction_id": "x"}).status_code == 503


# --------------------------------------------------------------- determinism + meta
def test_deterministic_responses(client):
    ps, ms = client._stores
    q = _seed(ps, ms, i=1, vector=_axis(0))
    _seed(ps, ms, i=2, vector=_axis(0))
    _seed(ps, ms, i=3, vector=_axis(1))
    a = client.get(f"/memory/similar/{q}").json()["neighbours"]
    b = client.get(f"/memory/similar/{q}").json()["neighbours"]
    assert [n["prediction_id"] for n in a] == [n["prediction_id"] for n in b]


def test_openapi_generates_all_similarity_paths():
    app = FastAPI()
    app.include_router(similarity_router)
    paths = app.openapi()["paths"]
    for expected in ("/memory/similar", "/memory/similar/health", "/memory/similar/search",
                     "/memory/similar/{prediction_id}"):
        assert expected in paths


def test_router_mounts_in_real_app():
    from app.api.main import app
    paths = {r.path for r in app.routes}
    assert "/memory/similar/{prediction_id}" in paths
    assert "/memory/similar/health" in paths and "/memory/similar/search" in paths


def test_api_module_does_not_import_engines():
    import ast

    import app.api.similarity as sim
    with open(sim.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
