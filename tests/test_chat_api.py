"""API tests for the Conversation REST layer (Sprint 6 · Milestone 7).

Mount the chat router over temporary stores (deps on app.state; and, separately, not available).
Cover every endpoint, request validation, session lifecycle, the full message pipeline (retrieval →
prompt → LLM), clarification routing, the error taxonomy (400/404/409/429/503), health + version,
deterministic responses, OpenAPI, and no-engine imports. Thin transport only.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import API_VERSION, router as chat_router
from app.conversation.llm_adapter import LLMAdapter, OpenAIProvider
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


def _app(stores, *, llm=None):
    ps, ms = stores
    app = FastAPI()
    app.state.forward_store = ps
    app.state.retrieval = RetrievalEngine(ps, ms)
    if llm is not None:
        app.state.conversation_llm = llm
    app.include_router(chat_router)
    return app


@pytest.fixture()
def client(stores):
    with TestClient(_app(stores)) as c:
        c._stores = stores  # type: ignore[attr-defined]
        yield c


@pytest.fixture()
def client_unavailable():
    app = FastAPI()
    app.include_router(chat_router)
    with TestClient(app) as c:
        yield c


def _seed(ps, *, i=1, symbol="RELIANCE.NS"):
    rec = PredictionRecord(
        symbol=symbol, exchange="NSE", timeframe="1d", current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0, target1=110.0,
        direction_prob=0.61, outcome_prob=0.62, sector="Energy", market_regime="BULL",
        prediction_model_version="pred-1", feature_version="feat-1", status=PredictionStatus.ACTIVE)
    rec.created_at = f"2026-01-01T00:00:{i:02d}+00:00"
    ps.create(rec)
    ps.update_resolution(rec.prediction_id, status=PredictionStatus.TARGET_HIT, resolved_price=110.0,
                         resolution_reason="t", realised_r=2.0, holding_bars=5)
    return rec.prediction_id


# --------------------------------------------------------------- session lifecycle
def test_create_get_delete_session(client):
    created = client.post("/chat/session", json={"title": "t"}).json()
    sid = created["session_id"]
    assert created["status"] == "CREATED" and created["message_count"] == 0
    assert client.get(f"/chat/session/{sid}").json()["session_id"] == sid
    assert client.get("/chat/session/nope").status_code == 404
    closed = client.delete(f"/chat/session/{sid}")
    assert closed.status_code == 200 and closed.json()["status"] == "COMPLETED"


# --------------------------------------------------------------- message pipeline
def test_message_runs_full_pipeline(client):
    pid = _seed(client._stores[0])
    body = client.post("/chat/message", json={"message": f"explain prediction {pid}"}).json()
    assert body["intent"] == "EXPLAIN_PREDICTION" and body["next_step"] == "RETRIEVAL"
    assert body["conversation_status"] == "ACTIVE" and body["response"]        # stub LLM text
    assert any(c["kind"] == "decision" for c in body["citations"])
    assert body["versions"]["engine_version"] == "eng-1"


def test_message_missing_subject_clarifies(client):
    body = client.post("/chat/message", json={"message": "show me the evidence"}).json()
    assert body["next_step"] == "CLARIFY" and body["availability_status"] == "NOT_AVAILABLE"
    assert "prediction id or a symbol" in body["response"] and body["citations"] == []


def test_message_unknown_clarifies(client):
    body = client.post("/chat/message", json={"message": "the quick brown fox"}).json()
    assert body["intent"] == "UNKNOWN" and body["next_step"] == "CLARIFY"


def test_multi_turn_uses_memory(client):
    pid = _seed(client._stores[0])
    first = client.post("/chat/message", json={"message": f"explain prediction {pid}"}).json()
    sid = first["session_id"]
    second = client.post("/chat/message", json={"session_id": sid, "message": "and why the confidence?"}).json()
    assert second["intent"] == "WHY_CONFIDENCE" and second["next_step"] == "RETRIEVAL"


# --------------------------------------------------------------- validation / errors
def test_empty_message_is_400(client):
    assert client.post("/chat/message", json={"message": "   "}).status_code == 400


def test_unknown_session_is_404(client):
    r = client.post("/chat/message", json={"session_id": "ghost", "message": "explain prediction abcdef1234567890"})
    assert r.status_code == 404


def test_closed_session_message_is_409(client):
    sid = client.post("/chat/session", json={}).json()["session_id"]
    client.delete(f"/chat/session/{sid}")
    r = client.post("/chat/message", json={"session_id": sid, "message": "explain prediction abcdef1234567890"})
    assert r.status_code == 409


def test_duplicate_message_is_409(client):
    pid = _seed(client._stores[0])
    sid = client.post("/chat/session", json={}).json()["session_id"]
    msg = f"explain prediction {pid}"
    client.post("/chat/message", json={"session_id": sid, "message": msg})
    assert client.post("/chat/message", json={"session_id": sid, "message": msg}).status_code == 409


def test_dependencies_unavailable_is_503(client_unavailable):
    assert client_unavailable.post("/chat/message", json={"message": "hi there friend"}).status_code == 503


# --------------------------------------------------------------- llm error mapping
def test_llm_rate_limited_is_429(stores):
    class _Raiser:
        def complete(self, request):
            raise type("RateLimitError", (Exception,), {})("slow down")

    llm = LLMAdapter(OpenAIProvider(_Raiser()))
    with TestClient(_app(stores, llm=llm)) as c:
        pid = _seed(stores[0])
        r = c.post("/chat/message", json={"message": f"explain prediction {pid}"})
        assert r.status_code == 429


def test_llm_unavailable_is_503(stores):
    class _Raiser:
        def complete(self, request):
            raise type("APIConnectionError", (Exception,), {})("down")

    with TestClient(_app(stores, llm=LLMAdapter(OpenAIProvider(_Raiser())))) as c:
        pid = _seed(stores[0])
        assert c.post("/chat/message", json={"message": f"explain prediction {pid}"}).status_code == 503


# --------------------------------------------------------------- health / version
def test_health_ready(client):
    body = client.get("/chat/health").json()
    assert body["status"] == "ready" and body["components"]["llm_adapter"]["available"] is True
    assert body["versions"]["api_version"] == API_VERSION


def test_health_degraded_without_deps(client_unavailable):
    assert client_unavailable.get("/chat/health").json()["status"] == "degraded"


def test_version_endpoint(client):
    body = client.get("/chat/version").json()
    assert body["api_version"] == API_VERSION and body["versions"]["llm_adapter_version"] == "llm-1"


# --------------------------------------------------------------- determinism / OpenAPI / isolation
def test_deterministic_response(client):
    pid = _seed(client._stores[0])
    a = client.post("/chat/message", json={"message": f"explain prediction {pid}"}).json()
    b = client.post("/chat/message", json={"message": f"explain prediction {pid}"}).json()
    # different auto-created sessions, identical content ⇒ identical answer + citations
    assert a["response"] == b["response"] and a["citations"] == b["citations"]
    assert a["availability_status"] == b["availability_status"]


def test_openapi_lists_chat_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/chat/message", "/chat/session", "/chat/session/{session_id}", "/chat/health",
                 "/chat/version"):
        assert path in paths


def test_chat_api_imports_no_engine():
    import ast

    import app.api.chat as api
    with open(api.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
