"""Tests for Workflow Definition & Validation (Sprint 8 · Milestone 2).

Cover the static `DefinitionValidator` (valid workflow + every error code: duplicate/unknown/initial/
unreachable/no-terminal/self-loop/from-terminal/duplicate-transition/cyclic/invalid-policy/invalid-
agent-task), the `ValidationResult` (validate / validate_or_raise / checksum / round-trip), the
immutable `WorkflowRegistry` (register, duplicate rejection, ordering, lookup, discovery, round-trip),
and no-engine / no-agent imports. Static structure only — no execution, no predicate evaluation.
"""

from __future__ import annotations

import pytest

from app.workflow.definition import (
    WORKFLOW_DEFINITION_VERSION,
    DefinitionValidator,
    ValidationError,
    ValidationErrorCode,
    ValidationResult,
    WorkflowRegistry,
)
from app.workflow.models import (
    StepKind,
    TransitionKind,
    WorkflowDefinition,
    WorkflowStep,
    WorkflowTransition,
)

V = DefinitionValidator()


def _valid_definition() -> WorkflowDefinition:
    a = WorkflowStep.create(name="fetch", kind=StepKind.TASK, agent_task="get intelligence",
                            step_id="a")
    b = WorkflowStep.create(name="check", kind=StepKind.TASK, agent_task="assess", step_id="b")
    end = WorkflowStep.create(name="done", kind=StepKind.TERMINAL, step_id="end")
    trans = [WorkflowTransition.create(from_step="a", to_step="b"),
             WorkflowTransition.create(from_step="b", to_step="end",
                                       kind=TransitionKind.CONDITIONAL, condition="ok")]
    return WorkflowDefinition.create(name="flow", steps=[a, b, end], transitions=trans,
                                     initial_step="a")


def _raw_definition(steps, transitions, initial_step, name="flow") -> WorkflowDefinition:
    """Build a definition via from_dict so it bypasses M1 create() structural checks — lets us craft
    graphs the validator must catch (dangling refs, duplicate step ids, bad initial step)."""
    return WorkflowDefinition.from_dict({
        "definition_id": "d-raw", "name": name, "version": "wf-1",
        "steps": [s.to_dict() for s in steps], "transitions": [t.to_dict() for t in transitions],
        "initial_step": initial_step, "checksum": "", "metadata": {}})


def _step(sid, kind=StepKind.TASK, agent_task="t", config=None):
    return WorkflowStep.create(name=sid, kind=kind, agent_task=agent_task, step_id=sid,
                               config=config or {})


# --------------------------------------------------------------- valid
def test_valid_workflow_has_no_issues():
    result = V.validate(_valid_definition())
    assert result.valid and result.issues == () and result.codes == ()
    assert result.version == WORKFLOW_DEFINITION_VERSION
    V.validate_or_raise(_valid_definition())            # does not raise


# --------------------------------------------------------------- graph errors
def test_duplicate_step_and_unknown_initial():
    d = _raw_definition([_step("a"), _step("a"), _step("end", StepKind.TERMINAL, agent_task=None)],
                        [], initial_step="ghost")
    codes = V.validate(d).codes
    assert ValidationErrorCode.DUPLICATE_STEP in codes
    assert ValidationErrorCode.INVALID_INITIAL_STEP in codes


def test_unknown_transition_target():
    d = _raw_definition([_step("a"), _step("end", StepKind.TERMINAL, agent_task=None)],
                        [WorkflowTransition.create(from_step="a", to_step="ghost")], initial_step="a")
    assert ValidationErrorCode.UNKNOWN_STEP in V.validate(d).codes


def test_unreachable_step():
    # island: a->end reachable; 'orphan' has no inbound edge
    d = WorkflowDefinition.create(
        name="f",
        steps=[_step("a"), _step("orphan"), _step("end", StepKind.TERMINAL, agent_task=None)],
        transitions=[WorkflowTransition.create(from_step="a", to_step="end")], initial_step="a")
    result = V.validate(d)
    assert ValidationErrorCode.UNREACHABLE_STEP in result.codes
    assert any(i.subject == "orphan" for i in result.issues)


def test_no_terminal_reachable():
    # a<->b would cycle; use a->b->a? that's cyclic. Instead: a->b and b->a is cyclic.
    # For "no terminal": a->b, b->a is cyclic (separate). A DAG with no sink is impossible, so
    # force it: single step with a self-referencing... use two steps both with outgoing to each other
    # is cyclic. So model "no terminal reachable" via a reachable set whose only sink is unreachable.
    d = WorkflowDefinition.create(
        name="f", steps=[_step("a"), _step("b"), _step("end", StepKind.TERMINAL, agent_task=None)],
        transitions=[WorkflowTransition.create(from_step="a", to_step="b"),
                     WorkflowTransition.create(from_step="b", to_step="a")], initial_step="a")
    codes = V.validate(d).codes
    assert ValidationErrorCode.CYCLIC_GRAPH in codes        # a<->b is a cycle
    assert ValidationErrorCode.NO_TERMINAL_STEP in codes    # end is unreachable, no sink reachable


def test_cycle_detection():
    d = WorkflowDefinition.create(
        name="f", steps=[_step("a"), _step("b"), _step("c")],
        transitions=[WorkflowTransition.create(from_step="a", to_step="b"),
                     WorkflowTransition.create(from_step="b", to_step="c"),
                     WorkflowTransition.create(from_step="c", to_step="a")], initial_step="a")
    assert ValidationErrorCode.CYCLIC_GRAPH in V.validate(d).codes


def test_self_loop_and_from_terminal_transitions():
    d = _raw_definition(
        [_step("a"), _step("end", StepKind.TERMINAL, agent_task=None)],
        [WorkflowTransition.create(from_step="a", to_step="a"),          # self-loop
         WorkflowTransition.create(from_step="end", to_step="a")],       # out of terminal
        initial_step="a")
    codes = V.validate(d).codes
    assert codes.count(ValidationErrorCode.INVALID_TRANSITION) >= 2


def test_duplicate_transitions():
    d = WorkflowDefinition.create(
        name="f", steps=[_step("a"), _step("end", StepKind.TERMINAL, agent_task=None)],
        transitions=[WorkflowTransition.create(from_step="a", to_step="end"),
                     WorkflowTransition.create(from_step="a", to_step="end")], initial_step="a")
    assert ValidationErrorCode.DUPLICATE_TRANSITION in V.validate(d).codes


# --------------------------------------------------------------- policy + agent task
def test_retry_timeout_rollback_policy_validation():
    bad = _step("a", config={"retry": {"max_attempts": -1}, "timeout": {"seconds": 0},
                             "rollback": {"hook": ""}})
    d = WorkflowDefinition.create(
        name="f", steps=[bad, _step("end", StepKind.TERMINAL, agent_task=None)],
        transitions=[WorkflowTransition.create(from_step="a", to_step="end")], initial_step="a")
    codes = V.validate(d).codes
    assert codes.count(ValidationErrorCode.INVALID_POLICY) == 3

    good = _step("a", config={"retry": {"max_attempts": 3, "strategy": "exponential",
                                        "backoff_seconds": 2},
                              "timeout": {"seconds": 30}, "rollback": {"hook": "undo", "on": "failure"}})
    d2 = WorkflowDefinition.create(
        name="f", steps=[good, _step("end", StepKind.TERMINAL, agent_task=None)],
        transitions=[WorkflowTransition.create(from_step="a", to_step="end")], initial_step="a")
    assert V.validate(d2).valid


def test_invalid_agent_task():
    d = WorkflowDefinition.create(
        name="f", steps=[_step("a", agent_task="   "), _step("end", StepKind.TERMINAL, agent_task=None)],
        transitions=[WorkflowTransition.create(from_step="a", to_step="end")], initial_step="a")
    assert ValidationErrorCode.INVALID_AGENT_TASK in V.validate(d).codes


# --------------------------------------------------------------- result API
def test_validate_or_raise_raises_first_issue():
    d = _raw_definition([_step("a"), _step("a")], [], initial_step="a")
    with pytest.raises(ValidationError) as exc:
        V.validate_or_raise(d)
    assert exc.value.code is ValidationErrorCode.DUPLICATE_STEP


def test_result_deterministic_and_round_trip():
    d = _valid_definition()
    a, b = V.validate(d), V.validate(d)
    assert a.checksum == b.checksum
    assert ValidationResult.from_dict(a.to_dict()) == a
    bad = V.validate(_raw_definition([_step("a")],
                                     [WorkflowTransition.create(from_step="a", to_step="x")],
                                     initial_step="a"))
    assert ValidationResult.from_dict(bad.to_dict()) == bad


# --------------------------------------------------------------- registry
def test_registry_register_lookup_and_duplicate():
    d1 = _valid_definition()
    d2 = WorkflowDefinition.create(name="other", steps=[_step("x"),
                                   _step("end", StepKind.TERMINAL, agent_task=None)],
                                   transitions=[WorkflowTransition.create(from_step="x", to_step="end")],
                                   initial_step="x")
    reg = WorkflowRegistry.create([d1, d2])
    assert len(reg) == 2 and d1.definition_id in reg
    assert reg.get(d1.definition_id).definition_id == d1.definition_id
    assert reg.ids() == tuple(sorted([d1.definition_id, d2.definition_id]))
    with pytest.raises(ValidationError) as exc:
        reg.register(d1)
    assert exc.value.code is ValidationErrorCode.DUPLICATE_WORKFLOW


def test_registry_deterministic_order_and_round_trip():
    d1 = _valid_definition()
    d2 = WorkflowDefinition.create(name="z", steps=[_step("x"),
                                   _step("end", StepKind.TERMINAL, agent_task=None)],
                                   transitions=[WorkflowTransition.create(from_step="x", to_step="end")],
                                   initial_step="x")
    forward = WorkflowRegistry.create([d1, d2])
    reverse = WorkflowRegistry.create([d2, d1])
    assert forward.ids() == reverse.ids() and forward.checksum == reverse.checksum
    assert WorkflowRegistry.from_dict(forward.to_dict()).checksum == forward.checksum


def test_registry_rejects_unsupported_version():
    with pytest.raises(ValidationError):
        WorkflowRegistry(version="wfdef-999")


# --------------------------------------------------------------- isolation
def test_definition_import_no_engine_or_agent():
    import ast

    import app.workflow.definition as m
    with open(m.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.agent",
                 "app.decision_intelligence", "app.conversation", "app.memory", "app.similarity",
                 "app.learning", "app.forward_testing", "app.chat", "openai")
    for name in imported:
        assert not name.startswith(forbidden), f"M2 must not import {name}"
    assert "app.workflow.models" in imported
