"""API tests for the Agent Engine REST layer (Sprint 7 · Milestone 7).

Mount the /agent/* router on a bare app (the pipeline self-builds from the default registry — no
external deps). Cover endpoint routing, the plan → authorize → execute artifact-passing pipeline, the
suggest endpoint (advisory), session lifecycle, request validation, error normalization (planner /
permission / execution / malformed payloads → HTTP), version + health, deterministic responses,
OpenAPI, and no-engine imports. Thin transport only — no business logic in the routes.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agent import AGENT_API_VERSION, router as agent_router


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(agent_router)
    return TestClient(app)


# --------------------------------------------------------------- version / health
def test_version_and_health(client):
    v = client.get("/agent/version")
    assert v.status_code == 200
    body = v.json()
    assert body["api_version"] == AGENT_API_VERSION == "agent-api-1"
    assert body["versions"]["planner_version"] == "plan-1"
    assert body["versions"]["executor_version"] == "exec-1"

    h = client.get("/agent/health")
    assert h.status_code == 200 and h.json()["status"] == "ready"
    assert h.json()["components"]["tool_registry"]["tools"] >= 7


# --------------------------------------------------------------- pipeline
def test_plan_authorize_execute_pipeline(client):
    plan_resp = client.post("/agent/plan", json={"task": {"description": "show system status"}})
    assert plan_resp.status_code == 200
    plan = plan_resp.json()["plan"]
    assert [s["tool_id"] for s in plan["steps"]] == ["system.health", "system.version"]

    authz_resp = client.post("/agent/authorize", json={"plan": plan})
    assert authz_resp.status_code == 200
    authz = authz_resp.json()["authorization"]
    assert authz["overall"] == "ALLOWED"

    exec_resp = client.post("/agent/execute", json={"plan": plan, "authorization": authz})
    assert exec_resp.status_code == 200
    result = exec_resp.json()["execution_result"]
    assert result["overall"] == "SUCCESS"
    assert [s["outcome"] for s in result["steps"]] == ["SUCCESS", "SUCCESS"]


def test_plan_is_deterministic(client):
    a = client.post("/agent/plan", json={"task": {"description": "explain why", "metadata": {}}})
    b = client.post("/agent/plan", json={"task": {"description": "explain why"}})
    assert a.json()["plan"]["checksum"] == b.json()["plan"]["checksum"]


def test_execute_with_approval_flow(client):
    # request a write tool by id -> approval required -> execute grants it
    plan = client.post("/agent/plan", json={"task": {"description": "",
        "metadata": {"requested_tools": ["decision_intelligence.get"]}}}).json()["plan"]
    authz = client.post("/agent/authorize", json={"plan": plan}).json()["authorization"]
    # decision_intelligence.get is read-only -> ALLOWED, executes without approval
    result = client.post("/agent/execute", json={"plan": plan, "authorization": authz}).json()
    assert result["execution_result"]["overall"] == "SUCCESS"


# --------------------------------------------------------------- suggest (advisory)
def test_suggest_endpoint_is_advisory(client):
    resp = client.post("/agent/suggest", json={"task": {"description": "explain the prediction"}})
    assert resp.status_code == 200
    suggestion = resp.json()["suggestion"]
    assert suggestion["provider"] == "echo" and suggestion["error"] is None
    assert len(suggestion["suggested_tools"]) >= 7        # all catalog tools suggested by the stub


def test_suggest_validates_request(client):
    resp = client.post("/agent/suggest",
                       json={"task": {"description": "x"}, "temperature": 9.0})
    assert resp.status_code == 400


# --------------------------------------------------------------- session lifecycle
def test_session_lifecycle(client):
    created = client.post("/agent/session", json={"name": "explainer",
                                                  "task": {"description": "explain p1"}})
    assert created.status_code == 200
    sid = created.json()["session"]["session_id"]
    assert created.json()["session"]["state"] == "CREATED"

    got = client.get(f"/agent/session/{sid}")
    assert got.status_code == 200 and got.json()["session"]["session_id"] == sid

    deleted = client.delete(f"/agent/session/{sid}")
    assert deleted.status_code == 200 and deleted.json()["session"]["state"] == "CANCELLED"
    assert client.get(f"/agent/session/{sid}").status_code == 404


# --------------------------------------------------------------- error normalization
def test_unsupported_task_normalized(client):
    resp = client.post("/agent/plan", json={"task": {"description": "do something vague"}})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "UNSUPPORTED_TASK"


def test_plan_requires_task_content(client):
    resp = client.post("/agent/plan", json={"task": {"description": "   "}})
    assert resp.status_code == 400


def test_malformed_plan_rejected(client):
    resp = client.post("/agent/authorize", json={"plan": {"steps": "not-a-list"}})
    assert resp.status_code == 400


def test_execute_authorization_mismatch_normalized(client):
    plan_a = client.post("/agent/plan", json={"task": {"description": "system status"}}).json()["plan"]
    plan_b = client.post("/agent/plan",
                         json={"task": {"description": "learning performance"}}).json()["plan"]
    authz_b = client.post("/agent/authorize", json={"plan": plan_b}).json()["authorization"]
    resp = client.post("/agent/execute", json={"plan": plan_a, "authorization": authz_b})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "INVALID_EXECUTION"


def test_missing_session_is_404(client):
    assert client.get("/agent/session/nope").status_code == 404
    assert client.delete("/agent/session/nope").status_code == 404


def test_authorize_denied_policy_surfaces_in_body(client):
    plan = client.post("/agent/plan", json={"task": {"description": "system status"}}).json()["plan"]
    policy = {"version": "perm-1", "name": "deny-system", "default_level": "ALLOWED",
              "rules": [{"rule_id": "r1", "level": "DENIED", "tool_id": None, "category": "SYSTEM",
                         "capability": None, "reason": "blocked"}]}
    authz = client.post("/agent/authorize", json={"plan": plan, "policy": policy}).json()
    assert authz["authorization"]["overall"] == "DENIED"


# --------------------------------------------------------------- OpenAPI / isolation
def test_openapi_lists_agent_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/agent/plan", "/agent/authorize", "/agent/execute", "/agent/suggest",
                 "/agent/session", "/agent/session/{session_id}", "/agent/health", "/agent/version"):
        assert path in paths, f"missing route {path}"


def test_agent_api_import_no_engine():
    import ast

    import app.api.agent as m
    with open(m.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.decision_intelligence",
                 "app.conversation", "app.memory", "app.similarity", "app.learning",
                 "app.forward_testing", "app.chat", "openai")
    for name in imported:
        assert not name.startswith(forbidden), f"M7 must not import {name}"
