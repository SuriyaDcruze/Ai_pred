"""API tests for the Forward Testing endpoints (Sprint 1 · Milestone 4).

These tests mount **only** the ``/forward/*`` router over a throwaway
``prediction_history.db`` in ``tmp_path`` — they never run the full application lifespan
and never touch production data. Each test gets its own fresh store.

They also assert the two standing guarantees of this milestone:
* the router adds no model logic and imports nothing from the Prediction/Outcome engines;
* the router is actually wired into the real application.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.forward import router as forward_router
from app.forward_testing.engine import ForwardTestingEngine
from app.forward_testing.models import PredictionStatus
from app.forward_testing.store import PredictionStore


@pytest.fixture()
def store(tmp_path):
    """A PredictionStore backed by a temporary database (never production)."""
    s = PredictionStore(path=str(tmp_path / "prediction_history.db"))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(store):
    """A TestClient over a minimal app that mounts only the forward router."""
    app = FastAPI()
    app.state.forward_store = store
    app.state.forward_engine = ForwardTestingEngine(store)
    app.include_router(forward_router)
    with TestClient(app) as c:
        yield c


def _payload(**overrides):
    """A valid BUY recommendation payload; override any field per test."""
    body = {
        "symbol": "RELIANCE.NS",
        "exchange": "NSE",
        "timeframe": "1d",
        "current_price": 100.0,
        "direction": "BUY",
        "recommendation": "BUY",
        "created_candle_ts": 1_700_000_000,
        "entry": 100.0,
        "stop": 95.0,
        "target1": 110.0,
        "target2": 120.0,
        "direction_prob": 0.61,
        "outcome_prob": 0.55,
        "decision_score": 0.4,
        "sector": "Energy",
        "source": "manual",
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------- create
def test_create_returns_201_and_active_record(client):
    resp = client.post("/forward/prediction", json=_payload())
    assert resp.status_code == 201
    pred = resp.json()["prediction"]
    assert pred["symbol"] == "RELIANCE.NS"
    assert pred["recommendation"] == "BUY"
    assert pred["status"] == PredictionStatus.ACTIVE.value  # market entry → live at once
    assert pred["is_open"] is True
    assert pred["is_terminal"] is False
    assert pred["sector"] == "Energy"
    assert "prediction_id" in pred and pred["prediction_id"]


def test_create_duplicate_returns_409(client):
    assert client.post("/forward/prediction", json=_payload()).status_code == 201
    dup = client.post("/forward/prediction", json=_payload())
    assert dup.status_code == 409
    assert "already exists" in dup.json()["detail"].lower()


def test_create_lowercase_side_is_normalised(client):
    resp = client.post("/forward/prediction", json=_payload(direction="buy", recommendation="buy"))
    assert resp.status_code == 201
    assert resp.json()["prediction"]["recommendation"] == "BUY"


def test_create_context_round_trips_as_object(client):
    resp = client.post("/forward/prediction", json=_payload(context={"note": "breakout", "rr": 2}))
    assert resp.status_code == 201
    assert resp.json()["prediction"]["context"] == {"note": "breakout", "rr": 2}


# ----------------------------------------------------------------- validation
def test_wait_recommendation_is_rejected(client):
    resp = client.post("/forward/prediction", json=_payload(recommendation="WAIT"))
    assert resp.status_code == 422  # a WAIT is not a trade — cannot be forward-tested


def test_non_positive_price_is_rejected(client):
    resp = client.post("/forward/prediction", json=_payload(current_price=0))
    assert resp.status_code == 422


def test_unknown_direction_is_rejected(client):
    resp = client.post("/forward/prediction", json=_payload(direction="HOLD"))
    assert resp.status_code == 422


def test_probability_out_of_bounds_is_rejected(client):
    resp = client.post("/forward/prediction", json=_payload(direction_prob=1.5))
    assert resp.status_code == 422


# ------------------------------------------------------------------- retrieve
def test_get_by_id_matches_created(client):
    created = client.post("/forward/prediction", json=_payload()).json()["prediction"]
    resp = client.get(f"/forward/prediction/{created['prediction_id']}")
    assert resp.status_code == 200
    assert resp.json()["prediction"]["prediction_id"] == created["prediction_id"]


def test_get_unknown_id_returns_404(client):
    resp = client.get("/forward/prediction/does-not-exist")
    assert resp.status_code == 404


def test_stats_route_is_not_shadowed_by_id_route(client):
    # /forward/stats must resolve to the stats handler, not /prediction/{id}.
    resp = client.get("/forward/stats")
    assert resp.status_code == 200
    assert "win_rate" in resp.json()


# --------------------------------------------------------------------- active
def test_active_lists_open_and_filters_by_symbol(client):
    client.post("/forward/prediction", json=_payload())
    client.post("/forward/prediction", json=_payload(symbol="TCS.NS", created_candle_ts=1_700_000_100))

    all_open = client.get("/forward/active").json()
    assert all_open["count"] == 2

    only_tcs = client.get("/forward/active", params={"symbol": "TCS.NS"}).json()
    assert only_tcs["count"] == 1
    assert only_tcs["predictions"][0]["symbol"] == "TCS.NS"


# ------------------------------------------------------------------ completed
def test_completed_reflects_resolution(client, store):
    created = client.post("/forward/prediction", json=_payload()).json()["prediction"]
    assert client.get("/forward/completed").json()["count"] == 0

    # Resolve it directly through the store (the monitor's job in production).
    store.update_resolution(
        created["prediction_id"],
        status=PredictionStatus.TARGET_HIT,
        resolved_price=110.0,
        resolution_reason="target hit",
        realised_r=2.0,
        holding_bars=5,
    )

    completed = client.get("/forward/completed").json()
    assert completed["count"] == 1
    assert completed["predictions"][0]["status"] == PredictionStatus.TARGET_HIT.value
    assert client.get("/forward/active").json()["count"] == 0  # no longer open


def test_completed_limit_is_respected(client, store):
    for i in range(3):
        created = client.post(
            "/forward/prediction", json=_payload(created_candle_ts=1_700_000_000 + i)
        ).json()["prediction"]
        store.update_resolution(
            created["prediction_id"],
            status=PredictionStatus.STOP_HIT,
            resolved_price=95.0,
            resolution_reason="stop hit",
            realised_r=-1.0,
            holding_bars=2,
        )
    limited = client.get("/forward/completed", params={"limit": 2}).json()
    assert limited["count"] == 2


# ---------------------------------------------------------------------- stats
def test_stats_math_after_one_win(client, store):
    created = client.post("/forward/prediction", json=_payload()).json()["prediction"]
    store.update_resolution(
        created["prediction_id"],
        status=PredictionStatus.TARGET_HIT,
        resolved_price=110.0,
        resolution_reason="target hit",
        realised_r=2.0,
        holding_bars=5,
    )
    stats = client.get("/forward/stats").json()
    assert stats["resolved"] == 1
    assert stats["wins"] == 1
    assert stats["win_rate"] == 1.0
    assert stats["avg_r"] == 2.0


# -------------------------------------------------------------------- summary
def test_summary_no_data_when_nothing_resolved(client):
    body = client.get("/forward/summary").json()
    assert body["confidence"] == "no_data"
    assert "disclaimer" in body
    assert body["stats"]["resolved"] == 0


def test_summary_flags_insufficient_sample(client, store):
    created = client.post("/forward/prediction", json=_payload()).json()["prediction"]
    store.update_resolution(
        created["prediction_id"],
        status=PredictionStatus.TARGET_HIT,
        resolved_price=110.0,
        resolution_reason="target hit",
        realised_r=2.0,
        holding_bars=5,
    )
    body = client.get("/forward/summary").json()
    assert body["confidence"] == "insufficient_sample"
    assert body["stats"]["resolved"] == 1
    assert body["min_meaningful_sample"] == 50


# ----------------------------------------------------- guardrails / isolation
def test_router_does_not_import_prediction_or_outcome_engines():
    """M4 adds no model logic: the API layer must not *import* the engines.

    Checked against the AST (the actual ``import`` statements), not the source text —
    the docstring legitimately *names* the engine files to document the guarantee.
    """
    import ast

    import app.api.forward as fwd

    with open(fwd.__file__, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    banned = {"app.ai.sklearn_model", "app.ai.outcome_model"}
    assert banned.isdisjoint(imported), f"forward API must not import the engines: {imported}"


def test_router_is_wired_into_the_real_app():
    """The /forward/* routes are registered on the production application."""
    from app.api.main import app

    paths = {route.path for route in app.routes}
    for expected in (
        "/forward/prediction",
        "/forward/prediction/{prediction_id}",
        "/forward/active",
        "/forward/completed",
        "/forward/stats",
        "/forward/summary",
    ):
        assert expected in paths
