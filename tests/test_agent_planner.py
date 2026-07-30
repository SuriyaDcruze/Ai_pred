"""Tests for the deterministic Planner (Sprint 7 · Milestone 3).

Cover goal resolution (explicit / keyword / ambiguous / unsupported), rule- and request-based tool
selection, dependency ordering (layered topological sort), validation + the full planner error
taxonomy (UNSUPPORTED_TASK / TOOL_NOT_FOUND / TOOL_UNAVAILABLE / INVALID_PLAN / DEPENDENCY_ERROR),
deterministic repeatability, serialization round-trips, and no-engine imports. Planning only — no
execution, permissions, engine calls, or LLM.
"""

from __future__ import annotations

import pytest

from app.agent.models import AgentPlan, AgentTask
from app.agent.planner import (
    DEFAULT_PLANNING_RULES,
    PLANNER_VERSION,
    Planner,
    PlannerError,
    PlannerErrorCategory,
    PlanningResult,
    PlanningRule,
    PlanningStatus,
    PlanStepSpec,
)
from app.agent.tools import (
    ToolAvailability,
    ToolCategory,
    ToolDefinition,
    ToolRegistry,
    default_registry,
)


def _task(description: str = "", **metadata) -> AgentTask:
    return AgentTask.create(description=description, metadata=metadata or None)


# --------------------------------------------------------------- selection / goals
def test_plan_explicit_goal_success():
    planner = Planner(default_registry())
    result = planner.plan(_task(goal="system_status"))
    assert result.ok and result.goal == "system_status"
    assert result.selected_tools == ("system.health", "system.version")
    assert isinstance(result.plan, AgentPlan) and result.version == PLANNER_VERSION
    assert [s.tool_id for s in result.plan.steps] == ["system.health", "system.version"]


def test_plan_keyword_resolution():
    planner = Planner(default_registry())
    result = planner.plan(_task("Please explain why this prediction was made"))
    assert result.ok and result.goal == "explain_prediction"


def test_unknown_goal_and_no_match_are_unsupported():
    planner = Planner(default_registry())
    assert planner.plan(_task(goal="teleport")).error_category is PlannerErrorCategory.UNSUPPORTED_TASK
    assert planner.plan(_task("do something vague")).error_category is PlannerErrorCategory.UNSUPPORTED_TASK


def test_ambiguous_task_rejected():
    planner = Planner(default_registry())
    # "explain" -> explain_prediction and "health" -> system_status both match.
    result = planner.plan(_task("explain the system health"))
    assert result.error_category is PlannerErrorCategory.UNSUPPORTED_TASK
    assert "ambiguous" in (result.error_message or "")


def test_requested_tools_selection_with_dependencies():
    planner = Planner(default_registry())
    task = _task(requested_tools=["conversation.explain", "decision_intelligence.get"],
                 dependencies={"conversation.explain": ["decision_intelligence.get"]})
    result = planner.plan_or_raise(task)
    assert result.selected_tools == ("decision_intelligence.get", "conversation.explain")
    explain = result.plan.steps[1]
    assert explain.tool_id == "conversation.explain" and explain.depends_on == (0,)


# --------------------------------------------------------------- dependency ordering
def test_dependency_topological_ordering():
    planner = Planner(default_registry())
    result = planner.plan_or_raise(_task(goal="explain_prediction"))
    ids = [s.tool_id for s in result.plan.steps]
    # roots (sorted by id) precede the dependent explain step
    assert ids == ["decision_intelligence.get", "memory.get_history", "similarity.find_similar",
                   "conversation.explain"]
    assert result.plan.steps[-1].depends_on == (0, 1, 2)


def test_circular_dependency_detected():
    planner = Planner(default_registry())
    task = _task(requested_tools=["decision_intelligence.get", "memory.get_history"],
                 dependencies={"decision_intelligence.get": ["memory.get_history"],
                               "memory.get_history": ["decision_intelligence.get"]})
    result = planner.plan(task)
    assert result.error_category is PlannerErrorCategory.DEPENDENCY_ERROR
    assert "circular" in (result.error_message or "")


def test_dependency_on_unknown_tool_in_plan():
    planner = Planner(default_registry())
    task = _task(requested_tools=["system.health"],
                 dependencies={"system.health": ["system.version"]})  # version not in plan
    result = planner.plan(task)
    assert result.error_category is PlannerErrorCategory.DEPENDENCY_ERROR


# --------------------------------------------------------------- registry validation
def test_tool_not_found_and_unavailable():
    planner = Planner(default_registry())
    assert planner.plan(_task(requested_tools=["ghost.tool"])).error_category \
        is PlannerErrorCategory.TOOL_NOT_FOUND

    reg = ToolRegistry.create([
        ToolDefinition.create(tool_id="memory.get_history", name="M", category=ToolCategory.MEMORY,
                              supported_engine="memory", availability=ToolAvailability.DEPRECATED)])
    result = Planner(reg).plan(_task(requested_tools=["memory.get_history"]))
    assert result.error_category is PlannerErrorCategory.TOOL_UNAVAILABLE


def test_duplicate_step_rejected():
    planner = Planner(default_registry())
    result = planner.plan(_task(requested_tools=["system.health", "system.health"]))
    assert result.error_category is PlannerErrorCategory.INVALID_PLAN


def test_planner_rejects_duplicate_rule_goal():
    with pytest.raises(PlannerError) as exc:
        Planner(default_registry(), rules=[
            PlanningRule(goal="g", steps=(PlanStepSpec.of("system.health"),)),
            PlanningRule(goal="g", steps=(PlanStepSpec.of("system.version"),))])
    assert exc.value.category is PlannerErrorCategory.INVALID_PLAN


# --------------------------------------------------------------- determinism / serialization
def test_planning_is_deterministic():
    planner = Planner(default_registry())
    a = planner.plan(_task(goal="explain_prediction"))
    b = planner.plan(_task(goal="explain_prediction"))
    assert a.checksum == b.checksum and a.plan.checksum == b.plan.checksum


def test_result_round_trip_success_and_failure():
    planner = Planner(default_registry())
    ok = planner.plan(_task(goal="learning_summary"))
    assert PlanningResult.from_dict(ok.to_dict()) == ok
    err = planner.plan(_task(goal="nope"))
    assert err.status is PlanningStatus.ERROR
    assert PlanningResult.from_dict(err.to_dict()) == err


def test_default_rules_reference_real_catalog_tools():
    reg = default_registry()
    for rule in DEFAULT_PLANNING_RULES:
        for step in rule.steps:
            assert reg.has(step.tool_id), f"rule {rule.goal} references missing {step.tool_id}"


def test_plan_or_raise_raises_categorised_error():
    planner = Planner(default_registry())
    with pytest.raises(PlannerError) as exc:
        planner.plan_or_raise(_task(goal="teleport"))
    assert exc.value.category is PlannerErrorCategory.UNSUPPORTED_TASK


# --------------------------------------------------------------- isolation
def test_planner_import_no_engine():
    import ast

    import app.agent.planner as m
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
        assert not name.startswith(forbidden), f"M3 must not import {name}"
    assert {"app.agent.models", "app.agent.tools"} <= set(imported)
