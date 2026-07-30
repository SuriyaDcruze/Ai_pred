"""Tests for the LLM Planning Adapter (Sprint 7 · Milestone 6).

Cover the provider abstraction (echo stub + duck-typed OpenAI), the factory/registry, request +
response validation (schema / unsupported tool / duplicate / malformed dependency), provider-fault
normalization into the deterministic error taxonomy, advisory-only behaviour (suggestions are NOT
executable plans — the Planner remains the authority), serialization round-trips (latency excluded),
deterministic checksums, and no-engine / no-Planner imports.
"""

from __future__ import annotations

import pytest

from app.agent.models import AgentTask
from app.agent.planner import Planner
from app.agent.planning_llm import (
    PLANNING_ADAPTER_VERSION,
    EchoPlanningProvider,
    InvalidPlanningRequestError,
    InvalidPlanningResponseError,
    OpenAIPlanningProvider,
    PlanningLLMAdapter,
    PlanningLLMErrorCategory,
    PlanningProviderNotFoundError,
    PlanningRequest,
    PlanningResponse,
    SuggestedStep,
    available_planning_providers,
    create_planning_adapter,
    validate_planning_response,
)
from app.agent.tools import default_registry


def _request(tools=("system.health", "system.version"), **kw) -> PlanningRequest:
    task = AgentTask.create(description=kw.pop("description", "explain the system status"))
    return PlanningRequest.create(task=task, available_tools=tools, **kw)


# --------------------------------------------------------------- request validation
def test_request_validation():
    req = _request(model="gpt-x", temperature=0.5)
    assert req.model == "gpt-x" and req.available_tools == ("system.health", "system.version")
    with pytest.raises(InvalidPlanningRequestError):
        PlanningRequest.create(task=AgentTask.create(description="d"), model="")
    with pytest.raises(InvalidPlanningRequestError):
        PlanningRequest.create(task=AgentTask.create(description="d"), temperature=9.0)
    with pytest.raises(InvalidPlanningRequestError):
        PlanningRequest.create(task=AgentTask.create(description="d"),
                               available_tools=["a.b", "a.b"])   # duplicate


# --------------------------------------------------------------- echo provider / adapter
def test_echo_provider_is_offline_deterministic_advisory():
    adapter = create_planning_adapter("echo")
    req = _request()
    r1 = adapter.suggest(req)
    r2 = adapter.suggest(req)
    assert r1.ok and r1.checksum == r2.checksum          # deterministic
    assert r1.tool_ids == ("system.health", "system.version")
    assert r1.provider == "echo" and r1.provider_metadata.get("stub") is True
    assert adapter.version()["adapter_version"] == PLANNING_ADAPTER_VERSION
    assert adapter.health()["available"] is True


def test_suggestion_is_advisory_planner_is_authority():
    reg = default_registry()
    req = _request(tools=tuple(reg.ids()))
    response = create_planning_adapter("echo").suggest(req)
    # the suggestion only becomes a plan when the deterministic Planner validates it
    tool_ids, deps = response.as_requested_tools()
    task = AgentTask.create(description="", metadata={"requested_tools": list(tool_ids),
                                                      "dependencies": deps})
    plan = Planner(reg).plan_or_raise(task).plan
    assert set(s.tool_id for s in plan.steps) == set(tool_ids)   # Planner produced the real plan


# --------------------------------------------------------------- response validation
def test_validate_rejects_unsupported_duplicate_and_bad_dep():
    req = _request(tools=("system.health",))
    with pytest.raises(InvalidPlanningResponseError):    # unsupported tool
        validate_planning_response(
            PlanningResponse(suggested_tools=(SuggestedStep("other.tool"),), rationale="",
                             provider="p", provider_version="v"), req)
    with pytest.raises(InvalidPlanningResponseError):    # duplicate
        validate_planning_response(
            PlanningResponse(suggested_tools=(SuggestedStep("system.health"),
                                              SuggestedStep("system.health")),
                             rationale="", provider="p", provider_version="v"), req)
    with pytest.raises(InvalidPlanningResponseError):    # dep on non-suggested tool
        validate_planning_response(
            PlanningResponse(suggested_tools=(SuggestedStep("system.health", depends_on=("x.y",)),),
                             rationale="", provider="p", provider_version="v"), req)


def test_adapter_normalizes_invalid_response():
    class _BadClient:
        def suggest(self, request):
            return {"suggested_tools": [{"tool_id": "not.in.catalog"}], "rationale": "r"}

    adapter = PlanningLLMAdapter(OpenAIPlanningProvider(_BadClient()))
    response = adapter.suggest(_request(tools=("system.health",)))
    assert not response.ok
    assert response.error.category is PlanningLLMErrorCategory.INVALID_RESPONSE


def test_empty_tool_id_is_malformed():
    with pytest.raises(InvalidPlanningResponseError):
        SuggestedStep(tool_id="")


# --------------------------------------------------------------- provider fault normalization
class _RateLimitError(Exception):
    pass


class _TimeoutError(Exception):
    pass


def test_provider_faults_normalized():
    class _Boom:
        def __init__(self, exc):
            self._exc = exc

        def suggest(self, request):
            raise self._exc

    req = _request()
    rl = PlanningLLMAdapter(OpenAIPlanningProvider(_Boom(_RateLimitError()))).suggest(req)
    assert rl.error.category is PlanningLLMErrorCategory.RATE_LIMITED
    to = PlanningLLMAdapter(OpenAIPlanningProvider(_Boom(_TimeoutError()))).suggest(req)
    assert to.error.category is PlanningLLMErrorCategory.TIMEOUT
    # no client configured -> provider unavailable
    unavailable = PlanningLLMAdapter(OpenAIPlanningProvider(None)).suggest(req)
    assert unavailable.error.category is PlanningLLMErrorCategory.PROVIDER_UNAVAILABLE


def test_openai_provider_maps_valid_response():
    class _GoodClient:
        def suggest(self, request):
            return {"suggested_tools": [{"tool_id": "system.health"},
                                        {"tool_id": "system.version",
                                         "depends_on": ["system.health"]}],
                    "rationale": "check health first", "confidence": 0.9,
                    "planning_notes": ["ordered"], "latency_ms": 12.5}

    adapter = PlanningLLMAdapter(OpenAIPlanningProvider(_GoodClient()))
    response = adapter.suggest(_request())
    assert response.ok and response.tool_ids == ("system.health", "system.version")
    assert response.confidence == 0.9 and response.suggested_tools[1].depends_on == ("system.health",)


# --------------------------------------------------------------- factory / serialization
def test_factory_and_unknown_provider():
    assert set(available_planning_providers()) >= {"echo", "openai", "azure_openai"}
    with pytest.raises(PlanningProviderNotFoundError):
        create_planning_adapter("nope")


def test_request_and_response_round_trip_excludes_latency():
    req = _request(model="gpt-x")
    assert PlanningRequest.from_dict(req.to_dict()) == req
    response = create_planning_adapter("echo").suggest(req)
    got = PlanningResponse.from_dict(response.to_dict())
    assert got.stable_dict() == response.stable_dict() and got.checksum == response.checksum
    # latency is runtime-only: not part of the deterministic fingerprint
    assert "latency_ms" not in response.stable_dict()


# --------------------------------------------------------------- isolation
def test_planning_llm_import_no_engine_no_planner():
    import ast

    import app.agent.planning_llm as m
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
                 "app.forward_testing", "app.chat", "openai", "app.agent.planner")
    for name in imported:
        assert not name.startswith(forbidden), f"M6 must not import {name}"
    assert "app.agent.models" in imported
