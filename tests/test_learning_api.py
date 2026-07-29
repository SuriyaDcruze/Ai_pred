"""API tests for the Learning REST layer (Sprint 4 · Vol 15 · Milestone 5).

Mount the learning router over temporary stores (retrieval on ``app.state``; and, separately,
not available). Cover every endpoint, request validation, pagination, filtering, deterministic
ordering + checksums, the error taxonomy (400/404/409/422/503), the health + evidence endpoints,
concurrency, schema/learning-version validation, the response-metadata envelope, and the empty
corpus. Temporary databases only.
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.learning import API_SCHEMA_VERSION, router as learning_router
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.store import PredictionStore
from app.learning.models import DATASET_VERSION, LEARNING_VERSION
from app.memory.retrieval import RetrievalEngine
from app.memory.store import MemoryStore

_TS = 1_700_000_000
# Query defaults that make a small test corpus usable (corpus < 30, want VALIDATED at n>=10).
_Q = {"min_corpus": 1, "min_sample": 10, "min_evidence": 3}


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
def client(stores):
    ps, ms = stores
    app = FastAPI()
    app.state.retrieval = RetrievalEngine(ps, ms)
    app.include_router(learning_router)
    with TestClient(app) as c:
        c._stores = (ps, ms)  # type: ignore[attr-defined]
        yield c


@pytest.fixture()
def client_unavailable():
    app = FastAPI()                                    # no retrieval on state
    app.include_router(learning_router)
    with TestClient(app) as c:
        yield c


def _seed(ps, *, i, sector="Energy", regime="BULL", tf="1d", resolve_r=2.0):
    rec = PredictionRecord(
        symbol=f"S{i}.NS", exchange="NSE", timeframe=tf, current_price=100.0, direction="BUY",
        recommendation="BUY", created_candle_ts=_TS + i, entry=100.0, stop=95.0, target1=110.0,
        outcome_prob=0.62, sector=sector, market_regime=regime, prediction_model_version="pred-1",
        feature_version="feat-1", status=PredictionStatus.ACTIVE,
    )
    rec.created_at = f"2026-01-01T00:00:{i:02d}+00:00"
    ps.create(rec)
    ps.update_resolution(
        rec.prediction_id,
        status=PredictionStatus.TARGET_HIT if resolve_r > 0 else PredictionStatus.STOP_HIT,
        resolved_price=110.0 if resolve_r > 0 else 95.0, resolution_reason="t",
        realised_r=resolve_r, holding_bars=5,
    )
    return rec.prediction_id


def _seed_validated(ps, *, n=12, sector="Energy"):
    return [_seed(ps, i=i, sector=sector) for i in range(n)]


def _meta_ok(body):
    m = body["meta"]
    assert m["schema_version"] == DATASET_VERSION and m["learning_version"] == LEARNING_VERSION
    assert m["api_schema_version"] == API_SCHEMA_VERSION and m["generated_at"]


# --------------------------------------------------------------- health
def test_health_enabled(client):
    body = client.get("/learning/health").json()
    assert body["enabled"] is True and body["engine_status"] == "ready"
    assert body["learning_version"] == LEARNING_VERSION and body["schema_version"] == DATASET_VERSION
    assert body["last_run_at"] is None                 # nothing run yet


def test_health_reports_corpus_size(client):
    _seed_validated(client._stores[0], n=6)
    assert client.get("/learning/health").json()["corpus_size"] == 6


def test_health_unavailable(client_unavailable):
    body = client_unavailable.get("/learning/health").json()
    assert body["enabled"] is False and body["engine_status"] == "unavailable"


def test_retrieval_unavailable_is_503(client_unavailable):
    assert client_unavailable.get("/learning/summary").status_code == 503


# --------------------------------------------------------------- summary
def test_summary_empty_corpus_is_insufficient(client):
    body = client.get("/learning/summary").json()
    assert body["status"] == "INSUFFICIENT_DATA" and body["corpus_size"] == 0
    assert body["validated_pattern_count"] == 0 and body["recommendation_count"] == 0
    _meta_ok(body)


def test_summary_with_validated_corpus(client):
    _seed_validated(client._stores[0], n=12)
    body = client.get("/learning/summary", params=_Q).json()
    assert body["status"] == "VALIDATED" and body["corpus_size"] == 12
    assert body["validated_pattern_count"] >= 1 and body["recommendation_count"] >= 1
    assert set(body["checksums"]) == {"dataset", "validation", "recommendations"}


# --------------------------------------------------------------- patterns
def test_patterns_returns_validated(client):
    _seed_validated(client._stores[0], n=12)
    body = client.get("/learning/patterns", params=_Q).json()
    assert body["total"] >= 1 and body["items"]
    item = body["items"][0]
    assert item["status"] == "VALIDATED"
    assert set(item["confidence_interval"]) >= {"low", "high", "width", "quality"}
    assert item["significance"]["significant"] in (True, False)
    _meta_ok(body)


def test_patterns_filter_by_sector(client):
    ps = client._stores[0]
    for i in range(6):
        _seed(ps, i=i, sector="Energy")
    for i in range(6, 12):
        _seed(ps, i=i, sector="IT")
    body = client.get("/learning/patterns", params={**_Q, "sector": "Energy"}).json()
    assert body["items"] and all(
        it["grouping_key"] == "sector" and it["grouping_value"] == "Energy" for it in body["items"])


def test_patterns_filter_by_status_and_invalid(client):
    _seed_validated(client._stores[0], n=12)
    ok = client.get("/learning/patterns", params={**_Q, "status": "VALIDATED"})
    assert ok.status_code == 200 and all(it["status"] == "VALIDATED" for it in ok.json()["items"])
    bad = client.get("/learning/patterns", params={**_Q, "status": "NONSENSE"})
    assert bad.status_code == 400


def test_patterns_pagination_deterministic(client):
    _seed_validated(client._stores[0], n=12)
    full = client.get("/learning/patterns", params={**_Q, "limit": 100}).json()
    page1 = client.get("/learning/patterns", params={**_Q, "limit": 2, "offset": 0}).json()
    page2 = client.get("/learning/patterns", params={**_Q, "limit": 2, "offset": 2}).json()
    assert full["total"] == page1["total"] == page2["total"]
    assert len(page1["items"]) == 2
    keys = [it["pattern_key"] for it in full["items"]]
    assert keys == sorted(keys)                        # deterministic ordering
    assert [it["pattern_key"] for it in page1["items"]] == keys[:2]
    assert [it["pattern_key"] for it in page2["items"]] == keys[2:4]


def test_patterns_bad_pagination_is_422(client):
    assert client.get("/learning/patterns", params={**_Q, "limit": 0}).status_code == 422
    assert client.get("/learning/patterns", params={**_Q, "offset": -1}).status_code == 422


# --------------------------------------------------------------- statistics
def test_statistics_carry_full_stats(client):
    _seed_validated(client._stores[0], n=12)
    item = client.get("/learning/statistics", params=_Q).json()["items"][0]
    for field in ("sample_size", "confidence_interval", "significance", "correction_method",
                  "consistency_score"):
        assert field in item
    assert item["correction_method"] == "benjamini_hochberg"


# --------------------------------------------------------------- recommendations / evidence
def test_recommendations_shape(client):
    _seed_validated(client._stores[0], n=12)
    body = client.get("/learning/recommendations", params=_Q).json()
    assert body["items"]
    rec = body["items"][0]
    for field in ("evidence_count", "limitations", "recommendation_confidence", "statistical_basis",
                  "supporting_prediction_ids"):
        assert field in rec
    assert rec["limitations"] and rec["supporting_prediction_ids"]
    assert set(body["confidence_distribution"]) == {"HIGH", "MEDIUM", "LOW"}


def test_recommendations_filter_by_confidence(client):
    _seed_validated(client._stores[0], n=12)
    body = client.get("/learning/recommendations", params={**_Q, "confidence": "LOW"}).json()
    assert all(r["recommendation_confidence"] == "LOW" for r in body["items"])


def test_evidence_found_and_missing(client):
    _seed_validated(client._stores[0], n=12)
    rec_id = client.get("/learning/recommendations", params=_Q).json()["items"][0]["recommendation_id"]
    ev = client.get(f"/learning/evidence/{rec_id}", params=_Q).json()["evidence"]
    assert ev["recommendation_id"] == rec_id and ev["supporting_prediction_ids"]
    assert ev["pattern_key"] and ev["statistical_basis"]
    missing = client.get("/learning/evidence/does-not-exist", params=_Q)
    assert missing.status_code == 404


# --------------------------------------------------------------- run
def test_run_is_idempotent_and_deterministic(client):
    _seed_validated(client._stores[0], n=12)
    a = client.post("/learning/run", params=_Q).json()
    b = client.post("/learning/run", params=_Q).json()
    assert a["run_id"] == b["run_id"]                  # same corpus + params ⇒ same run id
    assert a["checksums"] == b["checksums"]
    assert a["status"] == "VALIDATED" and a["recommendation_count"] >= 1


def test_run_updates_last_run_timestamp(client):
    _seed_validated(client._stores[0], n=12)
    assert client.get("/learning/health").json()["last_run_at"] is None
    client.post("/learning/run", params=_Q)
    assert client.get("/learning/health").json()["last_run_at"] is not None


# --------------------------------------------------------------- error taxonomy / versions
def test_unknown_correction_is_400(client):
    assert client.get("/learning/summary", params={**_Q, "correction": "bogus"}).status_code == 400


def test_learning_version_mismatch_is_409(client):
    r = client.get("/learning/summary", params={**_Q, "learning_version": "lrn-999"})
    assert r.status_code == 409


def test_schema_version_validation(client):
    ok = client.get("/learning/summary", params={**_Q, "schema_version": DATASET_VERSION})
    assert ok.status_code == 200
    bad = client.get("/learning/summary", params={**_Q, "schema_version": "lds-999"})
    assert bad.status_code == 409


# --------------------------------------------------------------- determinism / concurrency
def test_deterministic_checksum_across_calls(client):
    _seed_validated(client._stores[0], n=12)
    checks = {client.get("/learning/patterns", params=_Q).json()["checksum"] for _ in range(5)}
    assert len(checks) == 1


def test_concurrent_requests_consistent(client):
    _seed_validated(client._stores[0], n=12)
    checksums: list[str] = []
    errors: list[Exception] = []

    def work() -> None:
        try:
            checksums.append(client.get("/learning/patterns", params=_Q).json()["checksum"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(checksums)) == 1


# --------------------------------------------------------------- OpenAPI / isolation
def test_openapi_lists_learning_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/learning/summary", "/learning/patterns", "/learning/statistics",
                 "/learning/recommendations", "/learning/evidence/{recommendation_id}",
                 "/learning/run", "/learning/health"):
        assert path in paths


def test_learning_api_does_not_import_engines():
    import ast

    import app.api.learning as lapi
    with open(lapi.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert {"app.ai.sklearn_model", "app.ai.outcome_model"}.isdisjoint(imported)
