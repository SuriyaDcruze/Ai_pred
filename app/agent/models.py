"""Domain models for the Agent Engine (Sprint 7 · Milestone 1).

The Agent Engine (a later concern) will plan and — under explicit permission — execute tool calls
against the existing read-only AEGIS engines. **Milestone 1 defines only the deterministic domain
model**: the agent, its session + lifecycle, tasks, plans, tool calls/results, execution steps,
permission requests, and an immutable audit trail. It performs **no execution, no tools, no routing,
no LLM, no permissions logic, and no planning** — just the data structures those later milestones
will use, with deterministic ids, checksums, and serialization.

Determinism: every id is a pure function of its content (never random), a SHA-256 checksum
fingerprints each aggregate (volatile timestamps excluded), and everything serialises round-trip.
The module imports nothing from any engine.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

#: The Agent Engine method/schema version. A shape/method change is a new version, never an edit.
AGENT_VERSION: str = "agt-1"


# --------------------------------------------------------------------------- enums
class AgentState(str, Enum):
    """The agent session's deterministic lifecycle state."""

    CREATED = "CREATED"
    PLANNING = "PLANNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExecutionState(str, Enum):
    """The execution state of a tool call / plan step / result."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class TaskStatus(str, Enum):
    """The status of an agent task."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PermissionDecision(str, Enum):
    """The decision on a permission request (the model only — no permission logic here)."""

    PENDING = "PENDING"
    GRANTED = "GRANTED"
    DENIED = "DENIED"


#: Allowed agent-lifecycle transitions (terminal states have none). A self-transition is a no-op.
_ALLOWED: dict[AgentState, set[AgentState]] = {
    AgentState.CREATED: {AgentState.PLANNING, AgentState.CANCELLED, AgentState.FAILED},
    AgentState.PLANNING: {AgentState.WAITING_FOR_APPROVAL, AgentState.EXECUTING, AgentState.FAILED,
                          AgentState.CANCELLED},
    AgentState.WAITING_FOR_APPROVAL: {AgentState.EXECUTING, AgentState.CANCELLED, AgentState.FAILED},
    AgentState.EXECUTING: {AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED},
    AgentState.COMPLETED: set(),
    AgentState.FAILED: set(),
    AgentState.CANCELLED: set(),
}


# --------------------------------------------------------------------------- errors
class AgentError(Exception):
    """Base class for every agent-domain error."""


class InvalidAgentTransitionError(AgentError):
    """An illegal agent-lifecycle transition."""


class InvalidToolCallError(AgentError):
    """A malformed tool call."""


class InvalidPlanError(AgentError):
    """A malformed plan (bad step sequence / dependency)."""


class SchemaConsistencyError(AgentError):
    """An aggregate's shape is inconsistent."""


class UnsupportedVersionError(AgentError):
    """An agent version this build does not support."""


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


# --------------------------------------------------------------------------- audit
@dataclass(frozen=True)
class AuditEntry:
    """An **immutable** record of one action — the audit primitive. `timestamp` is metadata only
    (excluded from the checksum), so the trail stays deterministic content-wise."""

    entry_id: str
    sequence: int
    actor: str
    event: str
    reason: str | None = None
    tool: str | None = None
    outcome: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, sequence: int, actor: str, event: str, reason: str | None = None,
               tool: str | None = None, outcome: str | None = None,
               metadata: dict[str, Any] | None = None) -> "AuditEntry":
        return cls(entry_id=_id("audit", sequence, actor, event, tool, outcome), sequence=sequence,
                   actor=actor, event=event, reason=reason, tool=tool, outcome=outcome,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"entry_id": self.entry_id, "sequence": self.sequence, "actor": self.actor,
                "event": self.event, "reason": self.reason, "tool": self.tool,
                "outcome": self.outcome, "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditEntry":
        return cls(entry_id=_get(data, "entry_id"), sequence=int(_get(data, "sequence", 0)),
                   actor=_get(data, "actor"), event=_get(data, "event"), reason=_get(data, "reason"),
                   tool=_get(data, "tool"), outcome=_get(data, "outcome"),
                   metadata=dict(_get(data, "metadata") or {}), timestamp=_get(data, "timestamp"))


# --------------------------------------------------------------------------- tool call / result
@dataclass(frozen=True)
class ToolCall:
    """A request to run a tool — **no execution occurs here**; this is the container the executor
    (a later milestone) will drive. `parameters` are the tool inputs; timestamps are metadata."""

    call_id: str
    tool_id: str
    parameters: dict[str, Any]
    state: ExecutionState = ExecutionState.PENDING
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    started_at: str | None = None
    finished_at: str | None = None

    def __post_init__(self) -> None:
        if not self.tool_id:
            raise InvalidToolCallError("tool_id is required")
        if not isinstance(self.state, ExecutionState):
            raise InvalidToolCallError(f"invalid state {self.state!r}")
        if self.retry_count < 0:
            raise InvalidToolCallError("retry_count must be >= 0")

    @classmethod
    def create(cls, *, tool_id: str, parameters: dict[str, Any] | None = None,
               state: ExecutionState = ExecutionState.PENDING, retry_count: int = 0,
               metadata: dict[str, Any] | None = None, call_index: int = 0) -> "ToolCall":
        params = parameters or {}
        return cls(call_id=_id("tool", tool_id, json.dumps(params, sort_keys=True), call_index),
                   tool_id=tool_id, parameters=params, state=state, retry_count=retry_count,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"call_id": self.call_id, "tool_id": self.tool_id, "parameters": self.parameters,
                "state": self.state.value, "retry_count": self.retry_count, "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "created_at": self.created_at, "started_at": self.started_at,
                "finished_at": self.finished_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolCall":
        return cls(call_id=_get(data, "call_id"), tool_id=_get(data, "tool_id"),
                   parameters=dict(_get(data, "parameters") or {}),
                   state=ExecutionState(_get(data, "state", ExecutionState.PENDING.value)),
                   retry_count=int(_get(data, "retry_count", 0)),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"),
                   started_at=_get(data, "started_at"), finished_at=_get(data, "finished_at"))


@dataclass(frozen=True)
class ToolResult:
    """The (future) outcome of a tool call — the data shape only; nothing is executed in M1."""

    result_id: str
    call_id: str
    state: ExecutionState
    success: bool
    output: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, call_id: str, state: ExecutionState, success: bool,
               output: dict[str, Any] | None = None, error: str | None = None,
               metadata: dict[str, Any] | None = None) -> "ToolResult":
        return cls(result_id=_id("result", call_id, state.value, success), call_id=call_id,
                   state=state, success=success, output=output, error=error, metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"result_id": self.result_id, "call_id": self.call_id, "state": self.state.value,
                "success": self.success, "output": self.output, "error": self.error,
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolResult":
        return cls(result_id=_get(data, "result_id"), call_id=_get(data, "call_id"),
                   state=ExecutionState(_get(data, "state")), success=bool(_get(data, "success")),
                   output=_get(data, "output"), error=_get(data, "error"),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- plan / step
@dataclass(frozen=True)
class ExecutionStep:
    """One ordered step of a plan — its target tool, expected inputs/outputs, and dependencies
    (earlier step sequence numbers). Deterministic; no execution."""

    step_id: str
    sequence: int
    tool_id: str
    expected_inputs: dict[str, Any] = field(default_factory=dict)
    expected_outputs: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[int, ...] = ()
    state: ExecutionState = ExecutionState.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise InvalidPlanError("step sequence must be >= 0")
        for dep in self.depends_on:
            if dep < 0 or dep >= self.sequence:
                raise InvalidPlanError(
                    f"step {self.sequence} depends_on {dep} which is not an earlier step")

    @classmethod
    def create(cls, *, sequence: int, tool_id: str, expected_inputs: dict[str, Any] | None = None,
               expected_outputs: dict[str, Any] | None = None,
               depends_on: "tuple[int, ...] | list[int]" = (),
               state: ExecutionState = ExecutionState.PENDING,
               metadata: dict[str, Any] | None = None) -> "ExecutionStep":
        return cls(step_id=_id("step", sequence, tool_id), sequence=sequence, tool_id=tool_id,
                   expected_inputs=expected_inputs or {}, expected_outputs=expected_outputs or {},
                   depends_on=tuple(depends_on), state=state, metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "sequence": self.sequence, "tool_id": self.tool_id,
                "expected_inputs": self.expected_inputs, "expected_outputs": self.expected_outputs,
                "depends_on": list(self.depends_on), "state": self.state.value,
                "metadata": self.metadata}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionStep":
        return cls(step_id=_get(data, "step_id"), sequence=int(_get(data, "sequence", 0)),
                   tool_id=_get(data, "tool_id"), expected_inputs=dict(_get(data, "expected_inputs") or {}),
                   expected_outputs=dict(_get(data, "expected_outputs") or {}),
                   depends_on=tuple(_get(data, "depends_on") or ()),
                   state=ExecutionState(_get(data, "state", ExecutionState.PENDING.value)),
                   metadata=dict(_get(data, "metadata") or {}))


@dataclass(frozen=True)
class AgentPlan:
    """An ordered, deterministic plan of execution steps (no planning logic here — M1 is the shape)."""

    plan_id: str
    steps: tuple[ExecutionStep, ...]
    version: str
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, steps: "list[ExecutionStep] | tuple[ExecutionStep, ...]",
               plan_id: str | None = None, version: str = AGENT_VERSION,
               metadata: dict[str, Any] | None = None) -> "AgentPlan":
        ordered = _validate_steps(tuple(steps))
        pid = plan_id or _id("plan", *[s.step_id for s in ordered])
        checksum = _checksum({"plan_id": pid, "version": version,
                              "steps": [s.stable_dict() for s in ordered]})
        return cls(plan_id=pid, steps=ordered, version=version, checksum=checksum,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "version": self.version,
                "steps": [s.stable_dict() for s in self.steps], "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentPlan":
        return cls(plan_id=_get(data, "plan_id"),
                   steps=tuple(ExecutionStep.from_dict(s) for s in (_get(data, "steps") or [])),
                   version=_get(data, "version", AGENT_VERSION), checksum=_get(data, "checksum", ""),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


def _validate_steps(steps: "tuple[ExecutionStep, ...]") -> "tuple[ExecutionStep, ...]":
    ordered = tuple(sorted(steps, key=lambda s: s.sequence))
    sequences = [s.sequence for s in ordered]
    if sequences != list(range(len(ordered))):
        raise InvalidPlanError(f"step sequences must be 0..n-1 without gaps, got {sequences}")
    return ordered


# --------------------------------------------------------------------------- permission request
@dataclass(frozen=True)
class PermissionRequest:
    """A request to run a tool that needs approval — the **model only**; no permission logic in M1."""

    request_id: str
    tool_id: str
    action: str
    reason: str | None = None
    decision: PermissionDecision = PermissionDecision.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)
    requested_at: str = field(default_factory=_utc_now_iso)
    decided_at: str | None = None

    @classmethod
    def create(cls, *, tool_id: str, action: str, reason: str | None = None,
               decision: PermissionDecision = PermissionDecision.PENDING,
               metadata: dict[str, Any] | None = None) -> "PermissionRequest":
        return cls(request_id=_id("perm", tool_id, action), tool_id=tool_id, action=action,
                   reason=reason, decision=decision, metadata=metadata or {})

    def decide(self, decision: PermissionDecision) -> "PermissionRequest":
        """Return a new request carrying the decision (functional update — no logic decides it here)."""
        return replace(self, decision=decision, decided_at=_utc_now_iso())

    def stable_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "tool_id": self.tool_id, "action": self.action,
                "reason": self.reason, "decision": self.decision.value, "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "requested_at": self.requested_at, "decided_at": self.decided_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PermissionRequest":
        return cls(request_id=_get(data, "request_id"), tool_id=_get(data, "tool_id"),
                   action=_get(data, "action"), reason=_get(data, "reason"),
                   decision=PermissionDecision(_get(data, "decision", PermissionDecision.PENDING.value)),
                   metadata=dict(_get(data, "metadata") or {}), requested_at=_get(data, "requested_at"),
                   decided_at=_get(data, "decided_at"))


# --------------------------------------------------------------------------- task / agent
@dataclass(frozen=True)
class AgentTask:
    """A task the agent works on."""

    task_id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, description: str, status: TaskStatus = TaskStatus.PENDING,
               task_id: str | None = None, metadata: dict[str, Any] | None = None) -> "AgentTask":
        return cls(task_id=task_id or _id("task", description), description=description, status=status,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "description": self.description, "status": self.status.value,
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentTask":
        return cls(task_id=_get(data, "task_id"), description=_get(data, "description", ""),
                   status=TaskStatus(_get(data, "status", TaskStatus.PENDING.value)),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


@dataclass(frozen=True)
class Agent:
    """The static definition of an agent — its identity, description, and the tools it is allowed to
    use. A deterministic id; no behaviour."""

    agent_id: str
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    version: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def create(cls, *, name: str, description: str = "",
               allowed_tools: "tuple[str, ...] | list[str]" = (), agent_id: str | None = None,
               version: str = AGENT_VERSION, metadata: dict[str, Any] | None = None) -> "Agent":
        return cls(agent_id=agent_id or _id("agent", name, version), name=name,
                   description=description, allowed_tools=tuple(allowed_tools), version=version,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "name": self.name, "description": self.description,
                "allowed_tools": sorted(self.allowed_tools), "version": self.version,
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Agent":
        return cls(agent_id=_get(data, "agent_id"), name=_get(data, "name", ""),
                   description=_get(data, "description", ""),
                   allowed_tools=tuple(_get(data, "allowed_tools") or ()),
                   version=_get(data, "version", AGENT_VERSION),
                   metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- session (aggregate)
def _session_checksum(session_id: str, agent_id: str, state: AgentState, task: AgentTask | None,
                      plan: AgentPlan | None, tool_calls: "tuple[ToolCall, ...]",
                      audit_log: "tuple[AuditEntry, ...]", version: str,
                      metadata: dict[str, Any]) -> str:
    return _checksum({
        "session_id": session_id, "agent_id": agent_id, "state": state.value,
        "task": task.stable_dict() if task else None, "plan": plan.stable_dict() if plan else None,
        "tool_calls": [c.stable_dict() for c in tool_calls],
        "audit_log": [a.stable_dict() for a in audit_log], "version": version, "metadata": metadata,
    })


@dataclass(frozen=True)
class AgentSession:
    """One agent run — the aggregate: lifecycle state, its task + plan, tool calls, and an immutable
    audit trail. Functional-update (frozen): every mutator returns a new session with a recomputed
    checksum (volatile `created_at` excluded)."""

    session_id: str
    agent_id: str
    state: AgentState
    task: AgentTask | None
    plan: AgentPlan | None
    tool_calls: tuple[ToolCall, ...]
    audit_log: tuple[AuditEntry, ...]
    version: str
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if self.version != AGENT_VERSION:
            raise UnsupportedVersionError(f"unsupported version {self.version!r}")
        if not self.session_id or not self.agent_id:
            raise SchemaConsistencyError("session_id and agent_id are required")
        if not isinstance(self.state, AgentState):
            raise InvalidAgentTransitionError(f"invalid state {self.state!r}")
        for index, entry in enumerate(self.audit_log):
            if entry.sequence != index:
                raise SchemaConsistencyError(f"audit sequence {entry.sequence} != slot {index}")

    @classmethod
    def create(cls, *, agent_id: str, session_id: str | None = None, task: AgentTask | None = None,
               version: str = AGENT_VERSION, metadata: dict[str, Any] | None = None) -> "AgentSession":
        if version != AGENT_VERSION:
            raise UnsupportedVersionError(f"unsupported version {version!r}")
        sid = session_id or uuid.uuid4().hex
        meta = metadata or {}
        return cls(session_id=sid, agent_id=agent_id, state=AgentState.CREATED, task=task, plan=None,
                   tool_calls=(), audit_log=(), version=version,
                   checksum=_session_checksum(sid, agent_id, AgentState.CREATED, task, None, (), (),
                                              version, meta), metadata=meta)

    # ---- functional updates ---------------------------------------------
    def _rebuilt(self, **changes: Any) -> "AgentSession":
        updated = replace(self, **changes)
        return replace(updated, checksum=_session_checksum(
            updated.session_id, updated.agent_id, updated.state, updated.task, updated.plan,
            updated.tool_calls, updated.audit_log, updated.version, updated.metadata))

    def transition(self, target: AgentState) -> "AgentSession":
        """Return a new session in ``target`` state (validates the lifecycle transition)."""
        if target is not self.state and target not in _ALLOWED[self.state]:
            raise InvalidAgentTransitionError(f"cannot transition {self.state.value} -> {target.value}")
        return self._rebuilt(state=target)

    def with_task(self, task: AgentTask) -> "AgentSession":
        return self._rebuilt(task=task)

    def with_plan(self, plan: AgentPlan) -> "AgentSession":
        return self._rebuilt(plan=plan)

    def add_tool_call(self, call: ToolCall) -> "AgentSession":
        return self._rebuilt(tool_calls=self.tool_calls + (call,))

    def audit(self, *, actor: str, event: str, reason: str | None = None, tool: str | None = None,
              outcome: str | None = None, metadata: dict[str, Any] | None = None) -> "AgentSession":
        """Append an immutable audit entry (auto-sequenced) and return a new session."""
        entry = AuditEntry.create(sequence=len(self.audit_log), actor=actor, event=event,
                                  reason=reason, tool=tool, outcome=outcome, metadata=metadata)
        return self._rebuilt(audit_log=self.audit_log + (entry,))

    # ---- serialization ---------------------------------------------------
    def stable_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "agent_id": self.agent_id, "state": self.state.value,
            "task": self.task.stable_dict() if self.task else None,
            "plan": self.plan.stable_dict() if self.plan else None,
            "tool_calls": [c.stable_dict() for c in self.tool_calls],
            "audit_log": [a.stable_dict() for a in self.audit_log], "version": self.version,
            "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.stable_dict(), "checksum": self.checksum, "created_at": self.created_at,
            "task": self.task.to_dict() if self.task else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "audit_log": [a.to_dict() for a in self.audit_log],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentSession":
        task = _get(data, "task")
        plan = _get(data, "plan")
        return cls(
            session_id=_get(data, "session_id"), agent_id=_get(data, "agent_id"),
            state=AgentState(_get(data, "state", AgentState.CREATED.value)),
            task=AgentTask.from_dict(task) if task else None,
            plan=AgentPlan.from_dict(plan) if plan else None,
            tool_calls=tuple(ToolCall.from_dict(c) for c in (_get(data, "tool_calls") or [])),
            audit_log=tuple(AuditEntry.from_dict(a) for a in (_get(data, "audit_log") or [])),
            version=_get(data, "version", AGENT_VERSION), checksum=_get(data, "checksum", ""),
            metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"),
        )
