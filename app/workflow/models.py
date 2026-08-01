"""Domain models for the Workflow Engine (Sprint 8 · Milestone 1).

The Workflow Engine (later milestones) orchestrates **many Agent Engine executions** into a durable,
resumable, auditable process. **Milestone 1 defines only the deterministic domain model**: the
workflow identity, its declarative definition (steps + transitions), a running session with a
validated lifecycle, the execution progress snapshot, an immutable ordered event history,
checkpoints, and the final result. It performs **no** runtime, transitions, scheduling, retries,
checkpoint storage, agent invocation, REST, persistence, or business logic — just the data structures
those later milestones will use, with deterministic ids, checksums, and serialization.

Determinism: every id is a pure function of its content (never random), a SHA-256 checksum
fingerprints each aggregate (volatile timestamps excluded), and everything serialises round-trip. The
module imports nothing from any engine — **not** the Prediction engine, **not** the Outcome engine,
and **not** the Agent Engine; its helpers are self-contained.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

#: The Workflow Engine method/schema version. A shape/method change is a new version, never an edit.
WORKFLOW_VERSION: str = "wf-1"


# --------------------------------------------------------------------------- enums
class WorkflowState(str, Enum):
    """The workflow session's deterministic lifecycle state."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepState(str, Enum):
    """The execution state of a single workflow step (the shape only — no runtime in M1)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class StepKind(str, Enum):
    """What a step represents (declarative; the runtime meaning is a later milestone)."""

    TASK = "TASK"            # invoke an Agent Engine execution (later)
    APPROVAL = "APPROVAL"    # an approval checkpoint (later)
    WAIT = "WAIT"            # a waiting state (later)
    PARALLEL = "PARALLEL"    # a fan-out/fan-in branch point (later)
    TERMINAL = "TERMINAL"    # a terminal step


class TransitionKind(str, Enum):
    """The kind of edge between two steps (declarative; evaluation is a later milestone)."""

    SEQUENTIAL = "SEQUENTIAL"
    CONDITIONAL = "CONDITIONAL"
    PARALLEL = "PARALLEL"


class WorkflowEventType(str, Enum):
    """The type of an immutable workflow event (the vocabulary; emission is a later milestone)."""

    CREATED = "CREATED"
    STARTED = "STARTED"
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_FAILED = "STEP_FAILED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    WAIT_STARTED = "WAIT_STARTED"
    TIMER_FIRED = "TIMER_FIRED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    PAUSED = "PAUSED"
    RESUMED = "RESUMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLBACK = "ROLLBACK"


class WorkflowOutcome(str, Enum):
    """The aggregate outcome of a finished workflow."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: Allowed workflow-lifecycle transitions (terminal states have none). A self-transition is a no-op.
_ALLOWED: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.CREATED: {WorkflowState.RUNNING, WorkflowState.CANCELLED, WorkflowState.FAILED},
    WorkflowState.RUNNING: {WorkflowState.WAITING, WorkflowState.PAUSED, WorkflowState.COMPLETED,
                            WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.WAITING: {WorkflowState.RUNNING, WorkflowState.PAUSED, WorkflowState.COMPLETED,
                            WorkflowState.FAILED, WorkflowState.CANCELLED},
    WorkflowState.PAUSED: {WorkflowState.RUNNING, WorkflowState.CANCELLED, WorkflowState.FAILED},
    WorkflowState.COMPLETED: set(),
    WorkflowState.FAILED: set(),
    WorkflowState.CANCELLED: set(),
}


# --------------------------------------------------------------------------- errors
class WorkflowError(Exception):
    """Base class for every workflow-domain error."""


class InvalidWorkflowTransitionError(WorkflowError):
    """An illegal workflow-lifecycle transition."""


class InvalidWorkflowDefinitionError(WorkflowError):
    """A malformed workflow definition (bad step / transition / initial step)."""


class InvalidWorkflowEventError(WorkflowError):
    """A malformed workflow event."""


class SchemaConsistencyError(WorkflowError):
    """An aggregate's shape is inconsistent."""


class UnsupportedVersionError(WorkflowError):
    """A workflow version this build does not support."""


# --------------------------------------------------------------------------- helpers (self-contained)
def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _id(*parts: Any) -> str:
    """A deterministic id — a function of its content, never random."""
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------- event
@dataclass(frozen=True)
class WorkflowEvent:
    """An **immutable** record of one workflow event — the audit/event primitive. `timestamp` is
    metadata only (excluded from the checksum), so the event history stays deterministic content-wise."""

    event_id: str
    sequence: int
    event_type: WorkflowEventType
    step_id: str | None = None
    reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, WorkflowEventType):
            raise InvalidWorkflowEventError(f"invalid event_type {self.event_type!r}")
        if self.sequence < 0:
            raise InvalidWorkflowEventError("event sequence must be >= 0")

    @classmethod
    def create(cls, *, sequence: int, event_type: WorkflowEventType, step_id: str | None = None,
               reason: str | None = None, payload: dict[str, Any] | None = None) -> "WorkflowEvent":
        return cls(event_id=_id("wfevent", sequence, event_type.value, step_id), sequence=sequence,
                   event_type=event_type, step_id=step_id, reason=reason, payload=payload or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "sequence": self.sequence,
                "event_type": self.event_type.value, "step_id": self.step_id, "reason": self.reason,
                "payload": self.payload}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowEvent":
        return cls(event_id=_get(data, "event_id"), sequence=int(_get(data, "sequence", 0)),
                   event_type=WorkflowEventType(_get(data, "event_type")),
                   step_id=_get(data, "step_id"), reason=_get(data, "reason"),
                   payload=dict(_get(data, "payload") or {}), timestamp=_get(data, "timestamp"))


# --------------------------------------------------------------------------- step / transition
@dataclass(frozen=True)
class WorkflowStep:
    """One declarative step of a workflow definition — its kind and (for a TASK) the agent task it
    will (later) drive. Deterministic; no execution."""

    step_id: str
    name: str
    kind: StepKind = StepKind.TASK
    agent_task: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise InvalidWorkflowDefinitionError("step name is required")
        if not isinstance(self.kind, StepKind):
            raise InvalidWorkflowDefinitionError(f"invalid step kind {self.kind!r}")

    @classmethod
    def create(cls, *, name: str, kind: StepKind = StepKind.TASK, agent_task: str | None = None,
               config: dict[str, Any] | None = None, step_id: str | None = None,
               metadata: dict[str, Any] | None = None) -> "WorkflowStep":
        return cls(step_id=step_id or _id("wfstep", name, kind.value), name=name, kind=kind,
                   agent_task=agent_task, config=config or {}, metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "name": self.name, "kind": self.kind.value,
                "agent_task": self.agent_task, "config": self.config, "metadata": self.metadata}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowStep":
        return cls(step_id=_get(data, "step_id"), name=_get(data, "name", ""),
                   kind=StepKind(_get(data, "kind", StepKind.TASK.value)),
                   agent_task=_get(data, "agent_task"), config=dict(_get(data, "config") or {}),
                   metadata=dict(_get(data, "metadata") or {}))


@dataclass(frozen=True)
class WorkflowTransition:
    """A declarative edge between two steps. `condition` is a **label only** (M1 stores it; the
    transition/branching engine evaluates it in a later milestone). No evaluation happens here."""

    transition_id: str
    from_step: str
    to_step: str
    kind: TransitionKind = TransitionKind.SEQUENTIAL
    condition: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.from_step or not self.to_step:
            raise InvalidWorkflowDefinitionError("transition requires from_step and to_step")
        if not isinstance(self.kind, TransitionKind):
            raise InvalidWorkflowDefinitionError(f"invalid transition kind {self.kind!r}")

    @classmethod
    def create(cls, *, from_step: str, to_step: str, kind: TransitionKind = TransitionKind.SEQUENTIAL,
               condition: str | None = None, transition_id: str | None = None,
               metadata: dict[str, Any] | None = None) -> "WorkflowTransition":
        return cls(transition_id=transition_id or _id("wftrans", from_step, to_step, kind.value, condition),
                   from_step=from_step, to_step=to_step, kind=kind, condition=condition,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"transition_id": self.transition_id, "from_step": self.from_step,
                "to_step": self.to_step, "kind": self.kind.value, "condition": self.condition,
                "metadata": self.metadata}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowTransition":
        return cls(transition_id=_get(data, "transition_id"), from_step=_get(data, "from_step"),
                   to_step=_get(data, "to_step"),
                   kind=TransitionKind(_get(data, "kind", TransitionKind.SEQUENTIAL.value)),
                   condition=_get(data, "condition"), metadata=dict(_get(data, "metadata") or {}))


# --------------------------------------------------------------------------- definition
@dataclass(frozen=True)
class WorkflowDefinition:
    """The declarative workflow graph — ordered steps + typed transitions + an initial step.
    Immutable + checksummed. M1 enforces **structural coherence** only (unique step ids, a known
    initial step, transitions referencing known steps); full DAG/reachability validation is a later
    milestone."""

    definition_id: str
    name: str
    version: str
    steps: tuple[WorkflowStep, ...]
    transitions: tuple[WorkflowTransition, ...]
    initial_step: str
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, name: str, steps: "list[WorkflowStep] | tuple[WorkflowStep, ...]",
               transitions: "list[WorkflowTransition] | tuple[WorkflowTransition, ...]" = (),
               initial_step: str | None = None, definition_id: str | None = None,
               version: str = WORKFLOW_VERSION,
               metadata: dict[str, Any] | None = None) -> "WorkflowDefinition":
        steps_t = tuple(steps)
        transitions_t = tuple(transitions)
        _validate_definition(steps_t, transitions_t, initial_step)
        first = initial_step or steps_t[0].step_id
        did = definition_id or _id("wfdef", name, version, *[s.step_id for s in steps_t])
        checksum = _checksum({"definition_id": did, "name": name, "version": version,
                              "steps": [s.stable_dict() for s in steps_t],
                              "transitions": [t.stable_dict() for t in transitions_t],
                              "initial_step": first})
        return cls(definition_id=did, name=name, version=version, steps=steps_t,
                   transitions=transitions_t, initial_step=first, checksum=checksum,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"definition_id": self.definition_id, "name": self.name, "version": self.version,
                "steps": [s.stable_dict() for s in self.steps],
                "transitions": [t.stable_dict() for t in self.transitions],
                "initial_step": self.initial_step, "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowDefinition":
        return cls(definition_id=_get(data, "definition_id"), name=_get(data, "name", ""),
                   version=_get(data, "version", WORKFLOW_VERSION),
                   steps=tuple(WorkflowStep.from_dict(s) for s in (_get(data, "steps") or [])),
                   transitions=tuple(WorkflowTransition.from_dict(t) for t in (_get(data, "transitions") or [])),
                   initial_step=_get(data, "initial_step", ""), checksum=_get(data, "checksum", ""),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


def _validate_definition(steps: "tuple[WorkflowStep, ...]",
                         transitions: "tuple[WorkflowTransition, ...]",
                         initial_step: str | None) -> None:
    if not steps:
        raise InvalidWorkflowDefinitionError("a workflow definition needs at least one step")
    ids = [s.step_id for s in steps]
    if len(ids) != len(set(ids)):
        raise InvalidWorkflowDefinitionError(f"duplicate step ids: {ids}")
    id_set = set(ids)
    if initial_step is not None and initial_step not in id_set:
        raise InvalidWorkflowDefinitionError(f"initial_step {initial_step!r} is not a known step")
    for transition in transitions:
        if transition.from_step not in id_set or transition.to_step not in id_set:
            raise InvalidWorkflowDefinitionError(
                f"transition {transition.from_step}->{transition.to_step} references an unknown step")


# --------------------------------------------------------------------------- execution (progress snapshot)
@dataclass(frozen=True)
class WorkflowExecution:
    """The deterministic progress snapshot of a run — the current step, per-step states, and the
    completed steps. **Data only** (no runtime); functional-update helpers never mutate."""

    execution_id: str
    definition_id: str
    current_step_id: str | None = None
    step_states: dict[str, StepState] = field(default_factory=dict)
    completed_steps: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, definition_id: str, current_step_id: str | None = None,
               step_states: dict[str, StepState] | None = None,
               completed_steps: "tuple[str, ...] | list[str]" = (),
               execution_id: str | None = None,
               metadata: dict[str, Any] | None = None) -> "WorkflowExecution":
        return cls(execution_id=execution_id or _id("wfexec", definition_id),
                   definition_id=definition_id, current_step_id=current_step_id,
                   step_states=dict(step_states or {}), completed_steps=tuple(completed_steps),
                   metadata=metadata or {})

    # ---- functional updates (never mutate) ------------------------------
    def with_current_step(self, step_id: str | None) -> "WorkflowExecution":
        return replace(self, current_step_id=step_id)

    def with_step_state(self, step_id: str, state: StepState) -> "WorkflowExecution":
        return replace(self, step_states={**self.step_states, step_id: state})

    def with_completed(self, step_id: str) -> "WorkflowExecution":
        if step_id in self.completed_steps:
            return self
        return replace(self, completed_steps=self.completed_steps + (step_id,))

    def stable_dict(self) -> dict[str, Any]:
        return {"execution_id": self.execution_id, "definition_id": self.definition_id,
                "current_step_id": self.current_step_id,
                "step_states": {k: v.value for k, v in self.step_states.items()},
                "completed_steps": list(self.completed_steps), "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowExecution":
        return cls(execution_id=_get(data, "execution_id"), definition_id=_get(data, "definition_id"),
                   current_step_id=_get(data, "current_step_id"),
                   step_states={k: StepState(v) for k, v in (_get(data, "step_states") or {}).items()},
                   completed_steps=tuple(_get(data, "completed_steps") or ()),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- checkpoint
@dataclass(frozen=True)
class WorkflowCheckpoint:
    """A deterministic, serialisable snapshot of a session for resume — the lifecycle state, the
    execution snapshot, and how many events had been applied (`event_cursor`). **M1 defines the
    object only** — capture, storage, and resume are later milestones. `created_at` excluded from the
    checksum."""

    checkpoint_id: str
    session_id: str
    sequence: int
    state: WorkflowState
    execution: WorkflowExecution
    event_cursor: int
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, session_id: str, sequence: int, state: WorkflowState,
               execution: WorkflowExecution, event_cursor: int,
               metadata: dict[str, Any] | None = None) -> "WorkflowCheckpoint":
        checksum = _checksum({"session_id": session_id, "sequence": sequence, "state": state.value,
                              "execution": execution.stable_dict(), "event_cursor": event_cursor})
        return cls(checkpoint_id=_id("wfckpt", session_id, sequence), session_id=session_id,
                   sequence=sequence, state=state, execution=execution, event_cursor=event_cursor,
                   checksum=checksum, metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"checkpoint_id": self.checkpoint_id, "session_id": self.session_id,
                "sequence": self.sequence, "state": self.state.value,
                "execution": self.execution.stable_dict(), "event_cursor": self.event_cursor,
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "execution": self.execution.to_dict(),
                "checksum": self.checksum, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowCheckpoint":
        return cls(checkpoint_id=_get(data, "checkpoint_id"), session_id=_get(data, "session_id"),
                   sequence=int(_get(data, "sequence", 0)),
                   state=WorkflowState(_get(data, "state", WorkflowState.CREATED.value)),
                   execution=WorkflowExecution.from_dict(_get(data, "execution") or {}),
                   event_cursor=int(_get(data, "event_cursor", 0)),
                   checksum=_get(data, "checksum", ""), metadata=dict(_get(data, "metadata") or {}),
                   created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- result
@dataclass(frozen=True)
class WorkflowResult:
    """The deterministic aggregate outcome of a finished workflow."""

    result_id: str
    session_id: str
    outcome: WorkflowOutcome
    completed_steps: tuple[str, ...] = ()
    failed_steps: tuple[str, ...] = ()
    output: dict[str, Any] | None = None
    error: str | None = None
    checksum: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, session_id: str, outcome: WorkflowOutcome,
               completed_steps: "tuple[str, ...] | list[str]" = (),
               failed_steps: "tuple[str, ...] | list[str]" = (),
               output: dict[str, Any] | None = None, error: str | None = None,
               metadata: dict[str, Any] | None = None) -> "WorkflowResult":
        completed = tuple(completed_steps)
        failed = tuple(failed_steps)
        checksum = _checksum({"session_id": session_id, "outcome": outcome.value,
                              "completed_steps": list(completed), "failed_steps": list(failed),
                              "output": output, "error": error})
        return cls(result_id=_id("wfresult", session_id, outcome.value), session_id=session_id,
                   outcome=outcome, completed_steps=completed, failed_steps=failed, output=output,
                   error=error, checksum=checksum, metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"result_id": self.result_id, "session_id": self.session_id,
                "outcome": self.outcome.value, "completed_steps": list(self.completed_steps),
                "failed_steps": list(self.failed_steps), "output": self.output, "error": self.error,
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowResult":
        return cls(result_id=_get(data, "result_id"), session_id=_get(data, "session_id"),
                   outcome=WorkflowOutcome(_get(data, "outcome", WorkflowOutcome.SUCCESS.value)),
                   completed_steps=tuple(_get(data, "completed_steps") or ()),
                   failed_steps=tuple(_get(data, "failed_steps") or ()), output=_get(data, "output"),
                   error=_get(data, "error"), checksum=_get(data, "checksum", ""),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- workflow (static identity)
@dataclass(frozen=True)
class Workflow:
    """The static identity of a workflow — a name/description bound to a `WorkflowDefinition`. A
    deterministic id; no behaviour."""

    workflow_id: str
    name: str
    description: str
    definition_id: str
    version: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, name: str, definition_id: str, description: str = "",
               workflow_id: str | None = None, version: str = WORKFLOW_VERSION,
               metadata: dict[str, Any] | None = None) -> "Workflow":
        return cls(workflow_id=workflow_id or _id("workflow", name, version), name=name,
                   description=description, definition_id=definition_id, version=version,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"workflow_id": self.workflow_id, "name": self.name, "description": self.description,
                "definition_id": self.definition_id, "version": self.version, "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Workflow":
        return cls(workflow_id=_get(data, "workflow_id"), name=_get(data, "name", ""),
                   description=_get(data, "description", ""),
                   definition_id=_get(data, "definition_id", ""),
                   version=_get(data, "version", WORKFLOW_VERSION),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- session (aggregate)
def _session_checksum(session_id: str, workflow_id: str, definition_id: str, state: WorkflowState,
                      execution: WorkflowExecution, event_log: "tuple[WorkflowEvent, ...]",
                      checkpoint: WorkflowCheckpoint | None, result: WorkflowResult | None,
                      version: str, metadata: dict[str, Any]) -> str:
    return _checksum({
        "session_id": session_id, "workflow_id": workflow_id, "definition_id": definition_id,
        "state": state.value, "execution": execution.stable_dict(),
        "event_log": [e.stable_dict() for e in event_log],
        "checkpoint": checkpoint.stable_dict() if checkpoint else None,
        "result": result.stable_dict() if result else None, "version": version, "metadata": metadata,
    })


@dataclass(frozen=True)
class WorkflowSession:
    """One workflow run — the aggregate: lifecycle state, its execution snapshot, an immutable ordered
    event history, the latest checkpoint, and the final result. Functional-update (frozen): every
    mutator returns a new session with a recomputed checksum (volatile `created_at` excluded). **No
    runtime, scheduling, or agent invocation in M1** — only deterministic domain-state updates."""

    session_id: str
    workflow_id: str
    definition_id: str
    state: WorkflowState
    execution: WorkflowExecution
    event_log: tuple[WorkflowEvent, ...]
    checkpoint: WorkflowCheckpoint | None
    result: WorkflowResult | None
    version: str
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if self.version != WORKFLOW_VERSION:
            raise UnsupportedVersionError(f"unsupported version {self.version!r}")
        if not self.session_id or not self.workflow_id or not self.definition_id:
            raise SchemaConsistencyError("session_id, workflow_id and definition_id are required")
        if not isinstance(self.state, WorkflowState):
            raise InvalidWorkflowTransitionError(f"invalid state {self.state!r}")
        for index, event in enumerate(self.event_log):
            if event.sequence != index:
                raise SchemaConsistencyError(f"event sequence {event.sequence} != slot {index}")

    @classmethod
    def create(cls, *, workflow_id: str, definition_id: str, execution: WorkflowExecution | None = None,
               session_id: str | None = None, version: str = WORKFLOW_VERSION,
               metadata: dict[str, Any] | None = None) -> "WorkflowSession":
        if version != WORKFLOW_VERSION:
            raise UnsupportedVersionError(f"unsupported version {version!r}")
        sid = session_id or _id("wfsession", workflow_id, definition_id)
        execu = execution or WorkflowExecution.create(definition_id=definition_id)
        meta = metadata or {}
        return cls(session_id=sid, workflow_id=workflow_id, definition_id=definition_id,
                   state=WorkflowState.CREATED, execution=execu, event_log=(), checkpoint=None,
                   result=None, version=version, metadata=meta,
                   checksum=_session_checksum(sid, workflow_id, definition_id, WorkflowState.CREATED,
                                              execu, (), None, None, version, meta))

    # ---- functional updates (never mutate) ------------------------------
    def _rebuilt(self, **changes: Any) -> "WorkflowSession":
        updated = replace(self, **changes)
        return replace(updated, checksum=_session_checksum(
            updated.session_id, updated.workflow_id, updated.definition_id, updated.state,
            updated.execution, updated.event_log, updated.checkpoint, updated.result,
            updated.version, updated.metadata))

    def transition(self, target: WorkflowState) -> "WorkflowSession":
        """Return a new session in ``target`` state (validates the lifecycle transition)."""
        if target is not self.state and target not in _ALLOWED[self.state]:
            raise InvalidWorkflowTransitionError(
                f"cannot transition {self.state.value} -> {target.value}")
        return self._rebuilt(state=target)

    def with_execution(self, execution: WorkflowExecution) -> "WorkflowSession":
        return self._rebuilt(execution=execution)

    def record_event(self, *, event_type: WorkflowEventType, step_id: str | None = None,
                     reason: str | None = None,
                     payload: dict[str, Any] | None = None) -> "WorkflowSession":
        """Append an immutable event (auto-sequenced) and return a new session."""
        event = WorkflowEvent.create(sequence=len(self.event_log), event_type=event_type,
                                     step_id=step_id, reason=reason, payload=payload)
        return self._rebuilt(event_log=self.event_log + (event,))

    def with_checkpoint(self, checkpoint: WorkflowCheckpoint) -> "WorkflowSession":
        return self._rebuilt(checkpoint=checkpoint)

    def with_result(self, result: WorkflowResult) -> "WorkflowSession":
        return self._rebuilt(result=result)

    # ---- serialization ---------------------------------------------------
    def stable_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "workflow_id": self.workflow_id,
            "definition_id": self.definition_id, "state": self.state.value,
            "execution": self.execution.stable_dict(),
            "event_log": [e.stable_dict() for e in self.event_log],
            "checkpoint": self.checkpoint.stable_dict() if self.checkpoint else None,
            "result": self.result.stable_dict() if self.result else None, "version": self.version,
            "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.stable_dict(), "checksum": self.checksum, "created_at": self.created_at,
            "execution": self.execution.to_dict(),
            "event_log": [e.to_dict() for e in self.event_log],
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "result": self.result.to_dict() if self.result else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowSession":
        checkpoint = _get(data, "checkpoint")
        result = _get(data, "result")
        return cls(
            session_id=_get(data, "session_id"), workflow_id=_get(data, "workflow_id"),
            definition_id=_get(data, "definition_id"),
            state=WorkflowState(_get(data, "state", WorkflowState.CREATED.value)),
            execution=WorkflowExecution.from_dict(_get(data, "execution") or {}),
            event_log=tuple(WorkflowEvent.from_dict(e) for e in (_get(data, "event_log") or [])),
            checkpoint=WorkflowCheckpoint.from_dict(checkpoint) if checkpoint else None,
            result=WorkflowResult.from_dict(result) if result else None,
            version=_get(data, "version", WORKFLOW_VERSION), checksum=_get(data, "checksum", ""),
            metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"),
        )
