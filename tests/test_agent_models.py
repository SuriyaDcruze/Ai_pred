"""Tests for the Agent domain model (Sprint 7 · Milestone 1).

Cover agent/session/task/plan/tool-call/result/step/permission/audit construction, deterministic
ids, the lifecycle state machine (valid + invalid transitions), plan validation, immutable audit
entries (auto-sequenced), checksum determinism, serialization round-trips, immutability, and no-engine
imports. Domain models only — no execution, tools, routing, LLM, permissions, or planning.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.agent.models import (
    AGENT_VERSION,
    Agent,
    AgentPlan,
    AgentSession,
    AgentState,
    AgentTask,
    AuditEntry,
    ExecutionState,
    ExecutionStep,
    InvalidAgentTransitionError,
    InvalidPlanError,
    InvalidToolCallError,
    PermissionDecision,
    PermissionRequest,
    SchemaConsistencyError,
    TaskStatus,
    ToolCall,
    ToolResult,
    UnsupportedVersionError,
)


# --------------------------------------------------------------- construction / deterministic ids
def test_agent_and_task_deterministic_ids():
    a = Agent.create(name="explainer", allowed_tools=["intelligence", "learning"])
    assert a.agent_id == Agent.create(name="explainer").agent_id and a.version == AGENT_VERSION
    t = AgentTask.create(description="explain p1")
    assert t.task_id == AgentTask.create(description="explain p1").task_id and t.status is TaskStatus.PENDING


def test_tool_call_deterministic_and_validated():
    c = ToolCall.create(tool_id="intelligence.get", parameters={"prediction_id": "p1"})
    assert c.call_id == ToolCall.create(tool_id="intelligence.get", parameters={"prediction_id": "p1"}).call_id
    assert c.state is ExecutionState.PENDING and c.retry_count == 0
    with pytest.raises(InvalidToolCallError):
        ToolCall(call_id="x", tool_id="", parameters={})


def test_create_session_is_created_state():
    s = AgentSession.create(agent_id="a1", session_id="s1")
    assert s.state is AgentState.CREATED and s.version == AGENT_VERSION
    assert s.tool_calls == () and s.audit_log == () and s.checksum


# --------------------------------------------------------------- lifecycle
def test_valid_lifecycle_flow():
    s = AgentSession.create(agent_id="a1", session_id="s1")
    s = s.transition(AgentState.PLANNING).transition(AgentState.WAITING_FOR_APPROVAL)
    s = s.transition(AgentState.EXECUTING).transition(AgentState.COMPLETED)
    assert s.state is AgentState.COMPLETED


def test_invalid_transition_rejected():
    s = AgentSession.create(agent_id="a1", session_id="s1")
    with pytest.raises(InvalidAgentTransitionError):
        s.transition(AgentState.COMPLETED)          # CREATED -> COMPLETED not allowed


def test_terminal_states_are_terminal():
    s = AgentSession.create(agent_id="a1", session_id="s1").transition(AgentState.CANCELLED)
    with pytest.raises(InvalidAgentTransitionError):
        s.transition(AgentState.EXECUTING)


# --------------------------------------------------------------- plan / step validation
def test_plan_orders_and_validates_steps():
    steps = [ExecutionStep.create(sequence=1, tool_id="b", depends_on=[0]),
             ExecutionStep.create(sequence=0, tool_id="a")]
    plan = AgentPlan.create(steps=steps)
    assert [s.sequence for s in plan.steps] == [0, 1] and plan.checksum
    assert plan.steps[1].depends_on == (0,)


def test_plan_rejects_gaps_and_forward_dependency():
    with pytest.raises(InvalidPlanError):
        AgentPlan.create(steps=[ExecutionStep.create(sequence=0, tool_id="a"),
                                ExecutionStep.create(sequence=2, tool_id="c")])   # gap
    with pytest.raises(InvalidPlanError):
        ExecutionStep.create(sequence=0, tool_id="a", depends_on=[1])             # forward dep


# --------------------------------------------------------------- audit
def test_audit_entries_are_sequenced_and_immutable():
    s = AgentSession.create(agent_id="a1", session_id="s1")
    s = s.audit(actor="agent", event="created")
    s = s.audit(actor="user", event="approved", tool="intelligence.get", outcome="GRANTED")
    assert [e.sequence for e in s.audit_log] == [0, 1]
    assert s.audit_log[1].outcome == "GRANTED"
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.audit_log[0].event = "x"                  # type: ignore[misc]


def test_permission_request_decide():
    p = PermissionRequest.create(tool_id="t", action="run", reason="explain")
    assert p.decision is PermissionDecision.PENDING
    decided = p.decide(PermissionDecision.GRANTED)
    assert decided.decision is PermissionDecision.GRANTED and decided.decided_at is not None
    assert p.decision is PermissionDecision.PENDING   # original unchanged (functional update)


# --------------------------------------------------------------- checksum / immutability
def test_session_checksum_deterministic():
    def build():
        s = AgentSession.create(agent_id="a1", session_id="s1")
        return s.transition(AgentState.PLANNING).audit(actor="agent", event="planning")
    assert build().checksum == build().checksum
    other = AgentSession.create(agent_id="a1", session_id="s1").transition(AgentState.CANCELLED)
    assert other.checksum != build().checksum


def test_session_is_frozen_and_functional():
    s0 = AgentSession.create(agent_id="a1", session_id="s1")
    s1 = s0.transition(AgentState.PLANNING)
    assert s0.state is AgentState.CREATED and s1.state is AgentState.PLANNING   # original unchanged
    with pytest.raises(dataclasses.FrozenInstanceError):
        s0.state = AgentState.PLANNING              # type: ignore[misc]


def test_unsupported_version_rejected():
    with pytest.raises(UnsupportedVersionError):
        AgentSession.create(agent_id="a1", version="agt-999")


def test_session_requires_ids():
    with pytest.raises(SchemaConsistencyError):
        AgentSession(session_id="", agent_id="a1", state=AgentState.CREATED, task=None, plan=None,
                     tool_calls=(), audit_log=(), version=AGENT_VERSION, checksum="c")


# --------------------------------------------------------------- serialization
def test_session_round_trip():
    task = AgentTask.create(description="explain p1", status=TaskStatus.ACTIVE)
    plan = AgentPlan.create(steps=[ExecutionStep.create(sequence=0, tool_id="intelligence.get")])
    s = (AgentSession.create(agent_id="a1", session_id="s1", task=task)
         .with_plan(plan)
         .add_tool_call(ToolCall.create(tool_id="intelligence.get", parameters={"prediction_id": "p1"}))
         .transition(AgentState.PLANNING)
         .audit(actor="agent", event="planned", tool="intelligence.get"))
    got = AgentSession.from_dict(s.to_dict())
    assert got == s and got.checksum == s.checksum and got.stable_dict() == s.stable_dict()


def test_component_round_trips():
    for obj in (AgentTask.create(description="d"),
                ExecutionStep.create(sequence=0, tool_id="t", depends_on=[]),
                ToolCall.create(tool_id="t", parameters={"a": 1}),
                ToolResult.create(call_id="c", state=ExecutionState.SUCCEEDED, success=True, output={"x": 1}),
                PermissionRequest.create(tool_id="t", action="run"),
                AuditEntry.create(sequence=0, actor="agent", event="e"),
                Agent.create(name="a", allowed_tools=["t"])):
        assert type(obj).from_dict(obj.to_dict()) == obj


# --------------------------------------------------------------- isolation
def test_agent_models_import_no_engine():
    import ast

    import app.agent.models as m
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
        assert not name.startswith(forbidden), f"M1 must not import {name}"
