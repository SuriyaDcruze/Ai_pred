"""Tests for the deterministic Permission Engine (Sprint 7 · Milestone 4).

Cover read-only vs write-capable authorization, the metadata safety floor (policy can only tighten,
never loosen), approval generation (PermissionRequest, left PENDING), denied operations, policy
validation + the error taxonomy (POLICY_ERROR / INVALID_PERMISSION / APPROVAL_REQUIRED /
PERMISSION_DENIED), deterministic evaluation, serialization round-trips, and no-engine imports.
Authorization only — no execution, engine calls, state mutation, or LLM.
"""

from __future__ import annotations

import pytest

from app.agent.models import PermissionDecision, PermissionRequest
from app.agent.permissions import (
    PERMISSION_ENGINE_VERSION,
    AuthorizationResult,
    PermissionEngine,
    PermissionEngineError,
    PermissionErrorCategory,
    PermissionLevel,
    PermissionPolicy,
    PermissionRule,
    default_policy,
)
from app.agent.planner import Planner
from app.agent.tools import (
    ToolCapability,
    ToolCategory,
    ToolDefinition,
    ToolRegistry,
    default_registry,
)
from app.agent.models import AgentTask


def _registry_with_write() -> ToolRegistry:
    reg = default_registry()
    return reg.register(ToolDefinition.create(
        tool_id="memory.annotate", name="Annotate", category=ToolCategory.MEMORY,
        supported_engine="memory", capability=ToolCapability.WRITE, permission_required=True))


def _plan(goal: str, registry: ToolRegistry):
    return Planner(registry).plan_or_raise(AgentTask.create(description="", metadata={"goal": goal})).plan


# --------------------------------------------------------------- read-only tools
def test_read_only_plan_is_allowed():
    reg = default_registry()
    result = PermissionEngine(reg).evaluate(_plan("system_status", reg))
    assert result.overall is PermissionLevel.ALLOWED and result.allowed
    assert all(d.level is PermissionLevel.ALLOWED for d in result.decisions)
    assert result.approvals == ()
    assert result.version == PERMISSION_ENGINE_VERSION


# --------------------------------------------------------------- write / floor / approval
def test_write_tool_forces_approval_and_emits_request():
    reg = _registry_with_write()
    task = AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})
    plan = Planner(reg).plan_or_raise(task).plan
    result = PermissionEngine(reg).evaluate(plan)
    assert result.overall is PermissionLevel.APPROVAL_REQUIRED
    decision = result.decisions[0]
    assert decision.floor_level is PermissionLevel.APPROVAL_REQUIRED
    assert isinstance(decision.request, PermissionRequest)
    assert decision.request.decision is PermissionDecision.PENDING       # never auto-approved
    assert result.approvals == (decision.request,)


def test_policy_cannot_loosen_write_floor():
    reg = _registry_with_write()
    # A permissive rule that would ALLOW the write tool must NOT bypass the floor.
    policy = PermissionPolicy.create(rules=[
        PermissionRule.create(level=PermissionLevel.ALLOWED, tool_id="memory.annotate")])
    task = AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})
    plan = Planner(reg).plan_or_raise(task).plan
    decision = PermissionEngine(reg, policy).evaluate(plan).decisions[0]
    assert decision.policy_level is PermissionLevel.ALLOWED
    assert decision.level is PermissionLevel.APPROVAL_REQUIRED               # floor wins


def test_policy_can_tighten_read_only_to_denied():
    reg = default_registry()
    policy = PermissionPolicy.create(rules=[
        PermissionRule.create(level=PermissionLevel.DENIED, category=ToolCategory.SYSTEM)])
    result = PermissionEngine(reg, policy).evaluate(_plan("system_status", reg))
    assert result.overall is PermissionLevel.DENIED
    assert all(d.level is PermissionLevel.DENIED for d in result.decisions)
    assert result.approvals == ()                                           # denied != approval


def test_capability_rule_requires_approval():
    reg = default_registry()
    policy = PermissionPolicy.create(rules=[
        PermissionRule.create(level=PermissionLevel.APPROVAL_REQUIRED,
                              capability=ToolCapability.READ_ONLY)])
    result = PermissionEngine(reg, policy).evaluate(_plan("learning_summary", reg))
    assert result.overall is PermissionLevel.APPROVAL_REQUIRED


# --------------------------------------------------------------- raising API
def test_authorize_or_raise_taxonomy():
    reg = _registry_with_write()
    task = AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})
    plan = Planner(reg).plan_or_raise(task).plan
    with pytest.raises(PermissionEngineError) as approval:
        PermissionEngine(reg).authorize_or_raise(plan)
    assert approval.value.category is PermissionErrorCategory.APPROVAL_REQUIRED

    deny = PermissionPolicy.create(rules=[
        PermissionRule.create(level=PermissionLevel.DENIED, tool_id="memory.annotate")])
    with pytest.raises(PermissionEngineError) as denied:
        PermissionEngine(reg, deny).authorize_or_raise(plan)
    assert denied.value.category is PermissionErrorCategory.PERMISSION_DENIED

    ok = PermissionEngine(reg).authorize_or_raise(_plan("system_status", reg))
    assert ok.allowed


def test_unknown_tool_is_policy_error():
    # Plan built against the full catalog, but the engine's registry is empty -> tools unknown.
    plan = _plan("system_status", default_registry())
    with pytest.raises(PermissionEngineError) as exc:
        PermissionEngine(ToolRegistry.create()).evaluate(plan)
    assert exc.value.category is PermissionErrorCategory.POLICY_ERROR


# --------------------------------------------------------------- policy validation
def test_policy_rejects_duplicate_rule_ids():
    rule = PermissionRule.create(level=PermissionLevel.DENIED, tool_id="a.b")
    with pytest.raises(PermissionEngineError) as exc:
        PermissionPolicy(version=PERMISSION_ENGINE_VERSION, rules=(rule, rule))
    assert exc.value.category is PermissionErrorCategory.POLICY_ERROR


def test_policy_rejects_unsupported_version():
    with pytest.raises(PermissionEngineError) as exc:
        PermissionPolicy(version="perm-999")
    assert exc.value.category is PermissionErrorCategory.POLICY_ERROR


# --------------------------------------------------------------- determinism / serialization
def test_evaluation_is_deterministic():
    reg = _registry_with_write()
    task = AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})
    plan = Planner(reg).plan_or_raise(task).plan
    a = PermissionEngine(reg).evaluate(plan)
    b = PermissionEngine(reg).evaluate(plan)
    assert a.checksum == b.checksum


def test_result_round_trip():
    reg = _registry_with_write()
    task = AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})
    plan = Planner(reg).plan_or_raise(task).plan
    result = PermissionEngine(reg).evaluate(plan)
    assert AuthorizationResult.from_dict(result.to_dict()) == result


def test_policy_round_trip():
    policy = PermissionPolicy.create(name="strict", default_level=PermissionLevel.APPROVAL_REQUIRED,
                                     rules=[
                                         PermissionRule.create(level=PermissionLevel.DENIED,
                                                               tool_id="memory.annotate"),
                                         PermissionRule.create(level=PermissionLevel.ALLOWED,
                                                               category=ToolCategory.SYSTEM)])
    got = PermissionPolicy.from_dict(policy.to_dict())
    assert got == policy and got.checksum == policy.checksum


def test_default_policy_is_allow_with_floor():
    policy = default_policy()
    assert policy.default_level is PermissionLevel.ALLOWED and policy.rules == ()


# --------------------------------------------------------------- isolation
def test_permissions_import_no_engine():
    import ast

    import app.agent.permissions as m
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
        assert not name.startswith(forbidden), f"M4 must not import {name}"
    assert {"app.agent.models", "app.agent.tools"} <= set(imported)
