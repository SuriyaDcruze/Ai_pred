"""Tests for the Forward Testing dashboard's data layer (Sprint 1 · Milestone 5).

The dashboard is a presentation layer over the ``/forward/*`` REST API, so its behaviour is
tested at two levels:

* **Data API** — the server-side aggregation that powers the dashboard: `/forward/breakdown`
  and the live-vs-backtest fields on `/forward/summary`, including empty and error states.
* **Analytics units** — the pure aggregation helpers in `app/api/forward_analytics.py`.
* **Static delivery** — that `forward.html` is served and declares the six required
  sections and its loading/empty/error states.

All tests use a temporary database; production data is never touched. There is no JS test
runner in this repository, so DOM-level component testing is covered structurally (the
page's sections + state handling are asserted) rather than via a headless browser.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.api import forward_analytics as analytics
from app.api.forward import router as forward_router
from app.forward_testing.engine import ForwardTestingEngine
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def store(tmp_path):
    s = PredictionStore(path=str(tmp_path / "prediction_history.db"))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(store):
    app = FastAPI()
    app.state.forward_store = store
    app.state.forward_engine = ForwardTestingEngine(store)
    app.include_router(forward_router)
    with TestClient(app) as c:
        yield c


def _seed(store, rows):
    """Insert and resolve predictions. rows: (symbol, sector, tf, regime, prob, r)."""
    for i, (symbol, sector, tf, regime, prob, r) in enumerate(rows):
        rec = PredictionRecord(
            symbol=symbol, exchange="NSE", timeframe=tf, current_price=100.0,
            direction="BUY", recommendation="BUY", created_candle_ts=1_700_000_000 + i,
            entry=100.0, stop=95.0, target1=110.0, outcome_prob=prob,
            sector=sector, market_regime=regime, status=PredictionStatus.ACTIVE,
        )
        store.create(rec)
        store.update_resolution(
            rec.prediction_id,
            status=PredictionStatus.TARGET_HIT if r > 0 else PredictionStatus.STOP_HIT,
            resolved_price=110.0 if r > 0 else 95.0,
            resolution_reason="target hit" if r > 0 else "stop hit",
            realised_r=r, holding_bars=5,
        )


# --------------------------------------------------------------------------- #
# Analytics units
# --------------------------------------------------------------------------- #
def test_confidence_bucket_labels():
    def rec(p):
        return PredictionRecord(
            symbol="X", exchange="NSE", timeframe="1d", current_price=1.0,
            direction="BUY", recommendation="BUY", created_candle_ts=1, outcome_prob=p,
        )
    assert analytics.confidence_bucket(rec(0.62)) == "0.60–0.70"
    assert analytics.confidence_bucket(rec(0.5)) == "0.50–0.60"
    assert analytics.confidence_bucket(rec(1.0)) == "0.90–1.00"  # top edge lands in last bucket
    assert analytics.confidence_bucket(rec(None)) is None


def test_aggregate_math_matches_store_semantics():
    recs = []
    for i, r in enumerate([2.0, 2.0, -1.0, -1.0]):  # 2 wins, 2 losses
        rec = PredictionRecord(
            symbol="X", exchange="NSE", timeframe="1d", current_price=1.0,
            direction="BUY", recommendation="BUY", created_candle_ts=i,
            realised_r=r, holding_bars=4,
        )
        recs.append(rec)
    a = analytics.aggregate(recs)
    assert a["resolved"] == 4
    assert a["wins"] == 2 and a["losses"] == 2
    assert a["win_rate"] == 0.5
    assert a["avg_r"] == 0.5 and a["expectancy"] == 0.5  # (2+2-1-1)/4
    assert a["avg_win_r"] == 2.0 and a["avg_loss_r"] == -1.0
    assert a["profit_factor"] == pytest.approx(4.0 / 2.0)  # gross 4 / gross 2
    assert a["total_r"] == 2.0


def test_aggregate_empty_is_all_none():
    a = analytics.aggregate([])
    assert a["resolved"] == 0
    assert a["win_rate"] is None and a["avg_r"] is None and a["profit_factor"] is None


def test_group_and_aggregate_unknown_dimension_raises():
    with pytest.raises(ValueError):
        analytics.group_and_aggregate([], "nonsense")


def test_live_vs_backtest_status_transitions():
    # no data
    assert analytics.live_vs_backtest(None, 0, backtest_win_rate=0.6)["status"] == "no_data"
    # small sample
    r = analytics.live_vs_backtest(0.7, 5, backtest_win_rate=0.6)
    assert r["status"] == "building_sample"
    assert r["difference"] == pytest.approx(0.1)
    # large + decisive (all wins → CI excludes 0.5)
    big = analytics.live_vs_backtest(0.9, 100, backtest_win_rate=0.6)
    assert big["status"] == "statistically_significant"
    # large but borderline (~0.5 → CI spans coin flip)
    mid = analytics.live_vs_backtest(0.5, 100, backtest_win_rate=0.6)
    assert mid["status"] == "inconclusive"


def test_live_vs_backtest_no_baseline_configured():
    r = analytics.live_vs_backtest(0.7, 40, backtest_win_rate=-1.0)
    assert r["baseline_configured"] is False
    assert r["backtest_win_rate"] is None and r["difference"] is None
    assert r["status"] == "statistically_significant"  # sample logic still runs


# --------------------------------------------------------------------------- #
# /forward/breakdown
# --------------------------------------------------------------------------- #
def test_breakdown_empty_state(client):
    body = client.get("/forward/breakdown", params={"by": "sector"}).json()
    assert body["resolved_total"] == 0
    assert body["groups"] == []


def test_breakdown_bad_dimension_returns_422(client):
    assert client.get("/forward/breakdown", params={"by": "nope"}).status_code == 422


def test_breakdown_by_sector(client, store):
    _seed(store, [
        ("A.NS", "Energy", "1d", "BULL", 0.62, 2.0),
        ("B.NS", "Energy", "1d", "BULL", 0.66, 2.0),
        ("C.NS", "IT", "1h", "BEAR", 0.55, -1.0),
    ])
    body = client.get("/forward/breakdown", params={"by": "sector"}).json()
    assert body["dimension"] == "sector"
    assert body["resolved_total"] == 3
    by_bucket = {g["bucket"]: g["stats"] for g in body["groups"]}
    assert by_bucket["Energy"]["resolved"] == 2
    assert by_bucket["Energy"]["win_rate"] == 1.0
    assert by_bucket["IT"]["win_rate"] == 0.0


def test_breakdown_confidence_buckets(client, store):
    _seed(store, [
        ("A.NS", "Energy", "1d", "BULL", 0.62, 2.0),
        ("B.NS", "IT", "1h", "BEAR", 0.71, 2.0),
    ])
    body = client.get("/forward/breakdown", params={"by": "confidence"}).json()
    buckets = {g["bucket"] for g in body["groups"]}
    assert "0.60–0.70" in buckets and "0.70–0.80" in buckets


def test_breakdown_all_dimensions_are_accepted(client, store):
    _seed(store, [("A.NS", "Energy", "1d", "BULL", 0.62, 2.0)])
    for by in analytics.available_dimensions():
        assert client.get("/forward/breakdown", params={"by": by}).status_code == 200


# --------------------------------------------------------------------------- #
# /forward/summary — live vs backtest
# --------------------------------------------------------------------------- #
def test_summary_empty_has_backtest_and_no_data(client):
    s = client.get("/forward/summary").json()
    assert s["backtest"]["configured"] is True          # documented default baseline
    assert s["live_vs_backtest"]["status"] == "no_data"
    assert s["expectancy"] is None
    assert "disclaimer" in s


def test_summary_live_vs_backtest_after_trades(client, store):
    _seed(store, [
        ("A.NS", "Energy", "1d", "BULL", 0.62, 2.0),
        ("B.NS", "Energy", "1d", "BULL", 0.66, 2.0),
        ("C.NS", "IT", "1h", "BEAR", 0.55, -1.0),
    ])
    s = client.get("/forward/summary").json()
    c = s["live_vs_backtest"]
    assert c["sample_size"] == 3
    assert c["live_win_rate"] == pytest.approx(2 / 3)
    assert c["backtest_win_rate"] is not None
    assert c["difference"] == pytest.approx(2 / 3 - c["backtest_win_rate"])
    assert c["status"] == "building_sample"  # < 30 resolved
    assert s["expectancy"] == pytest.approx(1.0)  # (2+2-1)/3


# --------------------------------------------------------------------------- #
# Static delivery + guardrails
# --------------------------------------------------------------------------- #
def _static_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "dashboard", "static")


def test_forward_html_is_served_and_declares_all_sections():
    app = FastAPI()
    app.mount("/dashboard", StaticFiles(directory=_static_dir(), html=True), name="dashboard")
    with TestClient(app) as c:
        resp = c.get("/dashboard/forward.html")
        assert resp.status_code == 200
        html = resp.text
    for section in ("Overview", "Live vs Backtest", "Performance Breakdown",
                    "Active Predictions", "Completed Predictions", "Prediction Timeline"):
        assert section in html, f"dashboard missing section: {section}"


def test_forward_html_handles_loading_empty_and_error_states():
    with open(os.path.join(_static_dir(), "forward.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert "Loading" in html            # loading state
    assert "emptyState" in html         # empty state handler
    assert "errState" in html and "Retry" in html  # error state + recovery
    # Consumes the API, never the DB directly.
    assert "/forward/summary" in html and "/forward/breakdown" in html
    assert "sqlite" not in html.lower() and ".db" not in html


def test_index_links_to_forward_dashboard():
    with open(os.path.join(_static_dir(), "index.html"), encoding="utf-8") as fh:
        assert "/dashboard/forward.html" in fh.read()


def test_analytics_does_not_import_the_engines():
    import ast

    import app.api.forward_analytics as fa
    with open(fa.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
