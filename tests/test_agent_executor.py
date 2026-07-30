"""Tests for the deterministic Executor (Sprint 7 · Milestone 5).

Cover successful execution, skipped (dependency + missing-approval) steps, denied steps,
approval-required steps (granted vs not), tool-failure handling, audit generation + ordering,
execution ordering, validation + the error taxonomy (INVALID_EXECUTION / APPROVAL_MISSING /
TOOL_FAILURE / TOOL_UNAVAILABLE), deterministic behaviour, serialization round-trips, no-engine
imports, and the replaceable invoker abstraction. Execution only — no planning, permission
evaluation, or LLM.
"""

from __future__ import annotations

import pytest

from app.agent.executor import (
    EXECUTOR_VERSION,
    EchoToolInvoker,
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionOutcome,
    ExecutionResult,
    Executor,
    StepExecution,
    ToolInvoker,
)
from app.agent.models import AgentTask, PermissionDecision, ToolCall, ToolResult
from app.agent.models import ExecutionState
from app.agent.permissions import (
    PermissionEngine,
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


def _plan(goal: str, registry: ToolRegistry):
    return Planner(registry).plan_or_raise(AgentTask.create(description="", metadata={"goal": goal})).plan


def _write_registry() -> ToolRegistry:
    return default_registry().register(ToolDefinition.create(
        tool_id="memory.annotate", name="Annotate", category=ToolCategory.MEMORY,
        supported_engine="memory", capability=ToolCapability.WRITE, permission_required=True))


def _grant(authorization):
    return tuple(d.request.decide(PermissionDecision.GRANTED)
                 for d in authorization.decisions if d.request is not None)


# --------------------------------------------------------------- success
def test_all_allowed_steps_execute():
    reg = default_registry()
    plan = _plan("system_status", reg)
    authz = PermissionEngine(reg).evaluate(plan)
    result = Executor(reg).execute(plan, authz)
    assert result.overall is ExecutionOutcome.SUCCESS and result.succeeded
    assert [s.outcome for s in result.steps] == [ExecutionOutcome.SUCCESS, ExecutionOutcome.SUCCESS]
    assert all(s.tool_result and s.tool_result.success for s in result.steps)
    assert result.version == EXECUTOR_VERSION
    assert result.authorization_checksum == authz.checksum


def test_execution_and_audit_follow_plan_order():
    reg = default_registry()
    plan = _plan("explain_prediction", reg)          # 3 roots + dependent explain
    authz = PermissionEngine(reg).evaluate(plan)
    result = Executor(reg).execute(plan, authz)
    assert [s.tool_id for s in result.steps] == [st.tool_id for st in plan.steps]
    assert [s.sequence for s in result.steps] == [0, 1, 2, 3]
    # audit ordering matches execution ordering: started/completed pairs per step, monotonic sequence
    assert [a.sequence for a in result.audit_log] == list(range(len(result.audit_log)))
    started = [a.tool for a in result.audit_log if a.event == "execution_started"]
    assert started == [s.tool_id for s in plan.steps]


# --------------------------------------------------------------- approval-required
def test_approval_required_skipped_without_grant():
    reg = _write_registry()
    plan = Planner(reg).plan_or_raise(
        AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})).plan
    authz = PermissionEngine(reg).evaluate(plan)
    result = Executor(reg).execute(plan, authz)                    # no approvals passed
    step = result.steps[0]
    assert step.outcome is ExecutionOutcome.SKIPPED
    assert step.error_category is ExecutionErrorCategory.APPROVAL_MISSING
    assert step.tool_result is None                                # remained unexecuted


def test_approval_required_executes_when_granted():
    reg = _write_registry()
    plan = Planner(reg).plan_or_raise(
        AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})).plan
    authz = PermissionEngine(reg).evaluate(plan)
    result = Executor(reg).execute(plan, authz, approvals=_grant(authz))
    assert result.steps[0].outcome is ExecutionOutcome.SUCCESS


def test_execute_or_raise_requires_all_approvals():
    reg = _write_registry()
    plan = Planner(reg).plan_or_raise(
        AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})).plan
    authz = PermissionEngine(reg).evaluate(plan)
    with pytest.raises(ExecutionError) as exc:
        Executor(reg).execute_or_raise(plan, authz)
    assert exc.value.category is ExecutionErrorCategory.APPROVAL_MISSING
    ok = Executor(reg).execute_or_raise(plan, authz, approvals=_grant(authz))
    assert ok.succeeded


# --------------------------------------------------------------- denied + dependency skip
def test_denied_step_not_executed():
    reg = default_registry()
    plan = _plan("system_status", reg)
    policy = PermissionPolicy.create(rules=[
        PermissionRule.create(level=PermissionLevel.DENIED, category=ToolCategory.SYSTEM)])
    authz = PermissionEngine(reg, policy).evaluate(plan)
    result = Executor(reg).execute(plan, authz)
    assert result.overall is ExecutionOutcome.DENIED
    assert all(s.outcome is ExecutionOutcome.DENIED and s.tool_result is None for s in result.steps)
    assert any(a.event == "execution_denied" for a in result.audit_log)


def test_dependency_skip_when_prerequisite_denied():
    reg = default_registry()
    plan = _plan("explain_prediction", reg)          # explain depends on the 3 roots
    # deny one root -> the dependent explain step must be SKIPPED
    policy = PermissionPolicy.create(rules=[
        PermissionRule.create(level=PermissionLevel.DENIED, tool_id="memory.get_history")])
    authz = PermissionEngine(reg, policy).evaluate(plan)
    result = Executor(reg).execute(plan, authz)
    by_id = {s.tool_id: s for s in result.steps}
    assert by_id["memory.get_history"].outcome is ExecutionOutcome.DENIED
    assert by_id["conversation.explain"].outcome is ExecutionOutcome.SKIPPED
    assert "dependency" in (by_id["conversation.explain"].reason or "")
    assert by_id["decision_intelligence.get"].outcome is ExecutionOutcome.SUCCESS


# --------------------------------------------------------------- tool failure
class _BoomInvoker(ToolInvoker):
    def invoke(self, tool, call, context):
        raise RuntimeError("boom")


class _SadInvoker(ToolInvoker):
    def invoke(self, tool, call, context):
        return ToolResult.create(call_id=call.call_id, state=ExecutionState.FAILED, success=False,
                                 error="nope")


def test_invoker_exception_is_tool_failure():
    reg = default_registry()
    plan = _plan("learning_summary", reg)
    authz = PermissionEngine(reg).evaluate(plan)
    result = Executor(reg, _BoomInvoker()).execute(plan, authz)
    assert result.overall is ExecutionOutcome.FAILED
    assert result.steps[0].error_category is ExecutionErrorCategory.TOOL_FAILURE
    assert any(a.event == "execution_failed" for a in result.audit_log)


def test_invoker_reported_failure_is_tool_failure():
    reg = default_registry()
    plan = _plan("learning_summary", reg)
    authz = PermissionEngine(reg).evaluate(plan)
    result = Executor(reg, _SadInvoker()).execute(plan, authz)
    assert result.steps[0].outcome is ExecutionOutcome.FAILED
    assert result.steps[0].error_category is ExecutionErrorCategory.TOOL_FAILURE


# --------------------------------------------------------------- validation
def test_authorization_plan_mismatch_rejected():
    reg = default_registry()
    plan_a = _plan("system_status", reg)
    plan_b = _plan("learning_summary", reg)
    authz_b = PermissionEngine(reg).evaluate(plan_b)
    with pytest.raises(ExecutionError) as exc:
        Executor(reg).execute(plan_a, authz_b)
    assert exc.value.category is ExecutionErrorCategory.INVALID_EXECUTION


def test_unknown_tool_in_plan_rejected():
    reg = default_registry()
    plan = _plan("system_status", reg)
    authz = PermissionEngine(reg).evaluate(plan)
    with pytest.raises(ExecutionError) as exc:
        Executor(ToolRegistry.create()).execute(plan, authz)   # empty registry
    assert exc.value.category is ExecutionErrorCategory.INVALID_EXECUTION


# --------------------------------------------------------------- determinism / serialization
def test_execution_is_deterministic_by_checksum():
    reg = default_registry()
    plan = _plan("explain_prediction", reg)
    authz = PermissionEngine(reg).evaluate(plan)
    a = Executor(reg).execute(plan, authz)
    b = Executor(reg).execute(plan, authz)
    assert a.checksum == b.checksum


def test_result_round_trip():
    reg = _write_registry()
    plan = Planner(reg).plan_or_raise(
        AgentTask.create(description="", metadata={"requested_tools": ["memory.annotate"]})).plan
    authz = PermissionEngine(reg).evaluate(plan)
    result = Executor(reg).execute(plan, authz, approvals=_grant(authz))
    assert ExecutionResult.from_dict(result.to_dict()) == result


def test_context_round_trip_and_functional_update():
    ctx = ExecutionContext(inputs={"a": 1})
    ctx2 = ctx.with_output("t.x", {"v": 2})
    assert ctx.outputs == {} and ctx2.outputs == {"t.x": {"v": 2}}     # original unchanged
    assert ExecutionContext.from_dict(ctx2.to_dict()) == ctx2


def test_echo_invoker_is_offline_and_deterministic():
    reg = default_registry()
    tool = reg.get("system.health")
    call = ToolCall.create(tool_id="system.health", parameters={})
    r1 = EchoToolInvoker().invoke(tool, call, ExecutionContext())
    r2 = EchoToolInvoker().invoke(tool, call, ExecutionContext())
    assert r1.success and r1.stable_dict() == r2.stable_dict()


# --------------------------------------------------------------- isolation
def test_executor_import_no_engine():
    import ast

    import app.agent.executor as m
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
        assert not name.startswith(forbidden), f"M5 must not import {name}"
    assert {"app.agent.models", "app.agent.permissions", "app.agent.tools"} <= set(imported)
