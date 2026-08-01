"""Tests for the Workflow domain model (Sprint 8 · Milestone 1).

Cover workflow/definition/step/transition/execution/event/checkpoint/result/session construction,
deterministic ids, the lifecycle state machine (valid + invalid transitions, terminal states),
definition structural validation, immutable auto-sequenced event history, checksum determinism,
serialization round-trips, functional-update immutability, versioning, and no-engine imports (incl.
the Agent Engine). Domain models only — no runtime, transitions, scheduling, checkpoints-store,
agent invocation, REST, or persistence.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.workflow.models import (
    WORKFLOW_VERSION,
    InvalidWorkflowDefinitionError,
    InvalidWorkflowEventError,
    InvalidWorkflowTransitionError,
    SchemaConsistencyError,
    StepKind,
    StepState,
    TransitionKind,
    UnsupportedVersionError,
    Workflow,
    WorkflowCheckpoint,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowExecution,
    WorkflowOutcome,
    WorkflowResult,
    WorkflowSession,
    WorkflowState,
    WorkflowStep,
    WorkflowTransition,
)


def _definition() -> WorkflowDefinition:
    a = WorkflowStep.create(name="fetch", kind=StepKind.TASK, agent_task="get intelligence")
    b = WorkflowStep.create(name="approve", kind=StepKind.APPROVAL)
    c = WorkflowStep.create(name="done", kind=StepKind.TERMINAL)
    trans = [WorkflowTransition.create(from_step=a.step_id, to_step=b.step_id),
             WorkflowTransition.create(from_step=b.step_id, to_step=c.step_id,
                                       kind=TransitionKind.CONDITIONAL, condition="approved")]
    return WorkflowDefinition.create(name="review-flow", steps=[a, b, c], transitions=trans)


def _session() -> WorkflowSession:
    d = _definition()
    wf = Workflow.create(name="review-flow", definition_id=d.definition_id)
    return WorkflowSession.create(workflow_id=wf.workflow_id, definition_id=d.definition_id)


# --------------------------------------------------------------- construction / deterministic ids
def test_deterministic_ids_and_versions():
    d1, d2 = _definition(), _definition()
    assert d1.definition_id == d2.definition_id and d1.checksum == d2.checksum
    assert d1.version == WORKFLOW_VERSION and d1.initial_step == d1.steps[0].step_id
    wf = Workflow.create(name="w", definition_id=d1.definition_id)
    assert wf.workflow_id == Workflow.create(name="w", definition_id=d1.definition_id).workflow_id


def test_step_and_transition_validation():
    with pytest.raises(InvalidWorkflowDefinitionError):
        WorkflowStep(step_id="x", name="", kind=StepKind.TASK)
    with pytest.raises(InvalidWorkflowDefinitionError):
        WorkflowTransition(transition_id="x", from_step="a", to_step="")


def test_create_session_is_created_state():
    s = _session()
    assert s.state is WorkflowState.CREATED and s.version == WORKFLOW_VERSION
    assert s.event_log == () and s.checkpoint is None and s.result is None and s.checksum


# --------------------------------------------------------------- definition validation
def test_definition_rejects_duplicates_and_bad_refs():
    a = WorkflowStep.create(name="a", step_id="s1")
    dup = WorkflowStep.create(name="b", step_id="s1")
    with pytest.raises(InvalidWorkflowDefinitionError):
        WorkflowDefinition.create(name="d", steps=[a, dup])
    good = WorkflowStep.create(name="a", step_id="s1")
    with pytest.raises(InvalidWorkflowDefinitionError):    # transition to unknown step
        WorkflowDefinition.create(name="d", steps=[good],
                                  transitions=[WorkflowTransition.create(from_step="s1", to_step="ghost")])
    with pytest.raises(InvalidWorkflowDefinitionError):    # unknown initial step
        WorkflowDefinition.create(name="d", steps=[good], initial_step="ghost")
    with pytest.raises(InvalidWorkflowDefinitionError):    # empty
        WorkflowDefinition.create(name="d", steps=[])


# --------------------------------------------------------------- lifecycle
def test_valid_lifecycle_flow():
    s = _session()
    s = s.transition(WorkflowState.RUNNING).transition(WorkflowState.WAITING)
    s = s.transition(WorkflowState.RUNNING).transition(WorkflowState.COMPLETED)
    assert s.state is WorkflowState.COMPLETED


def test_invalid_transition_rejected():
    with pytest.raises(InvalidWorkflowTransitionError):
        _session().transition(WorkflowState.COMPLETED)     # CREATED -> COMPLETED not allowed


def test_terminal_states_are_terminal():
    s = _session().transition(WorkflowState.CANCELLED)
    with pytest.raises(InvalidWorkflowTransitionError):
        s.transition(WorkflowState.RUNNING)


# --------------------------------------------------------------- events
def test_events_are_sequenced_and_immutable():
    s = _session()
    s = s.record_event(event_type=WorkflowEventType.CREATED)
    s = s.record_event(event_type=WorkflowEventType.STARTED, step_id="s1", reason="go")
    assert [e.sequence for e in s.event_log] == [0, 1]
    assert s.event_log[1].event_type is WorkflowEventType.STARTED
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.event_log[0].reason = "x"                        # type: ignore[misc]


def test_event_type_validation():
    with pytest.raises(InvalidWorkflowEventError):
        WorkflowEvent(event_id="e", sequence=0, event_type="NOPE")   # type: ignore[arg-type]


def test_session_rejects_broken_event_sequence():
    bad = WorkflowEvent.create(sequence=5, event_type=WorkflowEventType.CREATED)
    s = _session()
    with pytest.raises(SchemaConsistencyError):
        dataclasses.replace(s, event_log=(bad,))


# --------------------------------------------------------------- execution (functional data updates)
def test_execution_functional_updates():
    e0 = WorkflowExecution.create(definition_id="d1")
    e1 = e0.with_current_step("s1").with_step_state("s1", StepState.RUNNING).with_completed("s0")
    assert e0.current_step_id is None and e0.step_states == {}       # original unchanged
    assert e1.current_step_id == "s1" and e1.step_states == {"s1": StepState.RUNNING}
    assert e1.with_completed("s0").completed_steps == ("s0",)        # idempotent add


# --------------------------------------------------------------- checksum / immutability
def test_session_checksum_deterministic():
    def build():
        s = WorkflowSession.create(workflow_id="w1", definition_id="d1", session_id="s1")
        return s.transition(WorkflowState.RUNNING).record_event(event_type=WorkflowEventType.STARTED)
    assert build().checksum == build().checksum
    other = WorkflowSession.create(workflow_id="w1", definition_id="d1",
                                   session_id="s1").transition(WorkflowState.CANCELLED)
    assert other.checksum != build().checksum


def test_session_is_frozen_and_functional():
    s0 = _session()
    s1 = s0.transition(WorkflowState.RUNNING)
    assert s0.state is WorkflowState.CREATED and s1.state is WorkflowState.RUNNING   # original intact
    with pytest.raises(dataclasses.FrozenInstanceError):
        s0.state = WorkflowState.RUNNING                    # type: ignore[misc]


def test_unsupported_version_rejected():
    with pytest.raises(UnsupportedVersionError):
        WorkflowSession.create(workflow_id="w1", definition_id="d1", version="wf-999")


def test_session_requires_ids():
    with pytest.raises(SchemaConsistencyError):
        WorkflowSession(session_id="", workflow_id="w1", definition_id="d1",
                        state=WorkflowState.CREATED, execution=WorkflowExecution.create(definition_id="d1"),
                        event_log=(), checkpoint=None, result=None, version=WORKFLOW_VERSION, checksum="c")


# --------------------------------------------------------------- serialization
def test_session_round_trip_full():
    d = _definition()
    execu = WorkflowExecution.create(definition_id=d.definition_id,
                                     current_step_id=d.steps[0].step_id).with_step_state(
        d.steps[0].step_id, StepState.SUCCEEDED).with_completed(d.steps[0].step_id)
    checkpoint = WorkflowCheckpoint.create(session_id="s1", sequence=0, state=WorkflowState.RUNNING,
                                           execution=execu, event_cursor=1)
    result = WorkflowResult.create(session_id="s1", outcome=WorkflowOutcome.SUCCESS,
                                   completed_steps=[d.steps[0].step_id], output={"ok": True})
    s = (WorkflowSession.create(workflow_id="w1", definition_id=d.definition_id, session_id="s1",
                                execution=execu)
         .transition(WorkflowState.RUNNING)
         .record_event(event_type=WorkflowEventType.STEP_COMPLETED, step_id=d.steps[0].step_id)
         .with_checkpoint(checkpoint)
         .with_result(result))
    got = WorkflowSession.from_dict(s.to_dict())
    assert got == s and got.checksum == s.checksum and got.stable_dict() == s.stable_dict()


def test_component_round_trips():
    d = _definition()
    for obj in (d,
                d.steps[0], d.transitions[0],
                WorkflowExecution.create(definition_id=d.definition_id),
                WorkflowEvent.create(sequence=0, event_type=WorkflowEventType.CREATED),
                WorkflowCheckpoint.create(session_id="s1", sequence=0, state=WorkflowState.CREATED,
                                          execution=WorkflowExecution.create(definition_id="d1"),
                                          event_cursor=0),
                WorkflowResult.create(session_id="s1", outcome=WorkflowOutcome.FAILED,
                                      failed_steps=["s1"], error="boom"),
                Workflow.create(name="w", definition_id=d.definition_id)):
        assert type(obj).from_dict(obj.to_dict()) == obj


def test_checkpoint_and_result_checksums_deterministic():
    execu = WorkflowExecution.create(definition_id="d1")
    c1 = WorkflowCheckpoint.create(session_id="s1", sequence=0, state=WorkflowState.RUNNING,
                                   execution=execu, event_cursor=2)
    c2 = WorkflowCheckpoint.create(session_id="s1", sequence=0, state=WorkflowState.RUNNING,
                                   execution=execu, event_cursor=2)
    assert c1.checksum == c2.checksum
    r1 = WorkflowResult.create(session_id="s1", outcome=WorkflowOutcome.SUCCESS)
    r2 = WorkflowResult.create(session_id="s1", outcome=WorkflowOutcome.SUCCESS)
    assert r1.checksum == r2.checksum


# --------------------------------------------------------------- isolation
def test_workflow_models_import_no_engine_or_agent():
    import ast

    import app.workflow.models as m
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
        assert not name.startswith(forbidden), f"M1 must not import {name}"
