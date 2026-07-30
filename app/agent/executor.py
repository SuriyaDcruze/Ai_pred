"""Deterministic Executor for the Agent Engine (Sprint 7 · Milestone 5).

The Executor consumes an :class:`~app.agent.models.AgentPlan` together with the
:class:`~app.agent.permissions.AuthorizationResult` for that plan, executes the steps that are
authorized (``ALLOWED``, or ``APPROVAL_REQUIRED`` with an explicitly **granted**
:class:`~app.agent.models.PermissionRequest`), records an immutable audit trail, and aggregates a
deterministic :class:`ExecutionResult`. It performs **no** planning, **no** permission evaluation,
and **no** LLM calls.

Tools are invoked only through a replaceable :class:`ToolInvoker` abstraction, gated by the Tool
Registry — the Executor never touches an engine implementation. The bundled :class:`EchoToolInvoker`
is a deterministic, offline stub (engine-backed invokers are a later concern). Execution follows the
deterministic plan order; audit ordering matches execution ordering; a step whose own authorization
is ``DENIED``, whose approval is missing, or whose dependency did not succeed remains **unexecuted**.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Mapping

from app.agent.models import (
    AGENT_VERSION,
    AgentPlan,
    AuditEntry,
    ExecutionState,
    PermissionDecision,
    PermissionRequest,
    ToolCall,
    ToolResult,
    _checksum,
    _get,
    _utc_now_iso,
)
from app.agent.permissions import (
    PERMISSION_ENGINE_VERSION,
    AuthorizationResult,
    PermissionLevel,
)
from app.agent.tools import ToolAvailability, ToolDefinition, ToolRegistry

#: The Executor method/schema version. A shape/method change is a new version, never an edit.
EXECUTOR_VERSION: str = "exec-1"


# --------------------------------------------------------------------------- enums
class ExecutionOutcome(str, Enum):
    """The deterministic outcome of one execution step (and the aggregate run)."""

    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    DENIED = "DENIED"
    FAILED = "FAILED"


#: Severity order — the aggregate outcome is the *worst* step outcome.
_OUTCOME_SEVERITY: dict[ExecutionOutcome, int] = {
    ExecutionOutcome.SUCCESS: 0,
    ExecutionOutcome.SKIPPED: 1,
    ExecutionOutcome.DENIED: 2,
    ExecutionOutcome.FAILED: 3,
}


class ExecutionErrorCategory(str, Enum):
    """Deterministic execution error categories (the model-level taxonomy)."""

    EXECUTION_ERROR = "EXECUTION_ERROR"
    TOOL_FAILURE = "TOOL_FAILURE"
    APPROVAL_MISSING = "APPROVAL_MISSING"
    INVALID_EXECUTION = "INVALID_EXECUTION"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"


#: Deterministic audit event names (execution ordering is preserved by sequence).
EVENT_STARTED = "execution_started"
EVENT_COMPLETED = "execution_completed"
EVENT_SKIPPED = "execution_skipped"
EVENT_DENIED = "execution_denied"
EVENT_FAILED = "execution_failed"


# --------------------------------------------------------------------------- error
class ExecutionError(Exception):
    """A deterministic execution failure carrying an :class:`ExecutionErrorCategory`."""

    def __init__(self, category: ExecutionErrorCategory, message: str) -> None:
        super().__init__(f"{category.value}: {message}")
        self.category = category
        self.message = message


# --------------------------------------------------------------------------- execution context
@dataclass(frozen=True)
class ExecutionContext:
    """The read-only context threaded through a run — caller inputs plus the outputs accumulated from
    completed steps (keyed by ``tool_id``). Frozen; updates are functional."""

    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_output(self, tool_id: str, output: Any) -> "ExecutionContext":
        return replace(self, outputs={**self.outputs, tool_id: output})

    def stable_dict(self) -> dict[str, Any]:
        return {"inputs": self.inputs, "outputs": self.outputs, "metadata": self.metadata}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionContext":
        return cls(inputs=dict(_get(data, "inputs") or {}), outputs=dict(_get(data, "outputs") or {}),
                   metadata=dict(_get(data, "metadata") or {}))


# --------------------------------------------------------------------------- tool invocation
class ToolInvoker(ABC):
    """A replaceable tool-invocation abstraction. The Executor calls tools **only** through this
    interface (gated by the Tool Registry) and never accesses an engine directly."""

    @abstractmethod
    def invoke(self, tool: ToolDefinition, call: ToolCall,
               context: ExecutionContext) -> ToolResult:  # pragma: no cover - interface
        ...


class EchoToolInvoker(ToolInvoker):
    """A deterministic, offline invoker (the concrete stub). It runs no engine — it returns a
    reproducible :class:`ToolResult` echoing the tool id and parameters, so the execution pipeline is
    fully testable without any engine dependency."""

    def invoke(self, tool: ToolDefinition, call: ToolCall, context: ExecutionContext) -> ToolResult:
        return ToolResult.create(
            call_id=call.call_id, state=ExecutionState.SUCCEEDED, success=True,
            output={"tool_id": tool.tool_id, "echo": call.parameters},
            metadata={"invoker": "echo"})


# --------------------------------------------------------------------------- step execution
@dataclass(frozen=True)
class StepExecution:
    """The deterministic record of one step's execution — its outcome, the authorization level it was
    evaluated under, the tool call/result (when executed), and an error category on skip/deny/fail."""

    step_id: str
    sequence: int
    tool_id: str
    outcome: ExecutionOutcome
    level: PermissionLevel
    reason: str | None = None
    error_category: ExecutionErrorCategory | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None

    def stable_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "sequence": self.sequence, "tool_id": self.tool_id,
                "outcome": self.outcome.value, "level": self.level.value, "reason": self.reason,
                "error_category": self.error_category.value if self.error_category else None,
                "tool_call": self.tool_call.stable_dict() if self.tool_call else None,
                "tool_result": self.tool_result.stable_dict() if self.tool_result else None}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(),
                "tool_call": self.tool_call.to_dict() if self.tool_call else None,
                "tool_result": self.tool_result.to_dict() if self.tool_result else None}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepExecution":
        call = _get(data, "tool_call")
        result = _get(data, "tool_result")
        category = _get(data, "error_category")
        return cls(
            step_id=_get(data, "step_id"), sequence=int(_get(data, "sequence", 0)),
            tool_id=_get(data, "tool_id"),
            outcome=ExecutionOutcome(_get(data, "outcome", ExecutionOutcome.SKIPPED.value)),
            level=PermissionLevel(_get(data, "level", PermissionLevel.ALLOWED.value)),
            reason=_get(data, "reason"),
            error_category=ExecutionErrorCategory(category) if category else None,
            tool_call=ToolCall.from_dict(call) if call else None,
            tool_result=ToolResult.from_dict(result) if result else None)


def _result_checksum(plan_id: str, authorization_checksum: str, overall: ExecutionOutcome,
                     steps: "tuple[StepExecution, ...]", audit_log: "tuple[AuditEntry, ...]",
                     version: str, metadata: dict[str, Any]) -> str:
    return _checksum({
        "plan_id": plan_id, "authorization_checksum": authorization_checksum,
        "overall": overall.value, "steps": [s.stable_dict() for s in steps],
        "audit_log": [a.stable_dict() for a in audit_log], "version": version, "metadata": metadata,
    })


@dataclass(frozen=True)
class ExecutionResult:
    """The deterministic outcome of executing a plan — a record per step, an aggregate ``overall``
    outcome (the worst step outcome), and the immutable audit trail. Checksummed; round-trip."""

    plan_id: str
    authorization_checksum: str
    overall: ExecutionOutcome
    steps: tuple[StepExecution, ...]
    audit_log: tuple[AuditEntry, ...]
    version: str
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @property
    def succeeded(self) -> bool:
        return self.overall is ExecutionOutcome.SUCCESS

    @classmethod
    def create(cls, *, plan_id: str, authorization_checksum: str,
               steps: "tuple[StepExecution, ...]", audit_log: "tuple[AuditEntry, ...]",
               metadata: dict[str, Any] | None = None) -> "ExecutionResult":
        overall = ExecutionOutcome.SUCCESS
        for step in steps:
            if _OUTCOME_SEVERITY[step.outcome] > _OUTCOME_SEVERITY[overall]:
                overall = step.outcome
        meta = metadata or {}
        return cls(plan_id=plan_id, authorization_checksum=authorization_checksum, overall=overall,
                   steps=steps, audit_log=audit_log, version=EXECUTOR_VERSION, metadata=meta,
                   checksum=_result_checksum(plan_id, authorization_checksum, overall, steps,
                                             audit_log, EXECUTOR_VERSION, meta))

    def stable_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "authorization_checksum": self.authorization_checksum,
                "overall": self.overall.value, "steps": [s.stable_dict() for s in self.steps],
                "audit_log": [a.stable_dict() for a in self.audit_log], "version": self.version,
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "steps": [s.to_dict() for s in self.steps],
                "audit_log": [a.to_dict() for a in self.audit_log], "checksum": self.checksum,
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionResult":
        return cls(
            plan_id=_get(data, "plan_id"),
            authorization_checksum=_get(data, "authorization_checksum", ""),
            overall=ExecutionOutcome(_get(data, "overall", ExecutionOutcome.SUCCESS.value)),
            steps=tuple(StepExecution.from_dict(s) for s in (_get(data, "steps") or [])),
            audit_log=tuple(AuditEntry.from_dict(a) for a in (_get(data, "audit_log") or [])),
            version=_get(data, "version", EXECUTOR_VERSION), checksum=_get(data, "checksum", ""),
            metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- executor
class Executor:
    """A deterministic, policy-enforced executor. It runs only authorized steps, in plan order,
    through the :class:`ToolInvoker` abstraction, and records an immutable audit trail. It never
    plans, evaluates permissions, or calls an LLM."""

    version: str = EXECUTOR_VERSION

    def __init__(self, registry: ToolRegistry, invoker: ToolInvoker | None = None) -> None:
        self._registry = registry
        self._invoker = invoker or EchoToolInvoker()

    # ---- public API ------------------------------------------------------
    def execute(self, plan: AgentPlan, authorization: AuthorizationResult, *,
                approvals: "Iterable[PermissionRequest]" = (),
                context: ExecutionContext | None = None) -> ExecutionResult:
        """Execute ``plan`` under ``authorization``. Denied / unapproved / dependency-blocked steps
        remain unexecuted (``DENIED`` / ``SKIPPED``); a tool fault yields ``FAILED``. Structural
        faults (mismatched or incomplete authorization, unknown tool) raise ``INVALID_EXECUTION``."""
        by_step = self._validate(plan, authorization)
        granted = {r.request_id for r in approvals if r.decision is PermissionDecision.GRANTED}
        ctx = context or ExecutionContext()

        steps: list[StepExecution] = []
        audit: list[AuditEntry] = []
        outcomes: dict[str, ExecutionOutcome] = {}
        ordered = plan.steps  # AgentPlan guarantees sequence 0..n-1 order

        for step in ordered:
            decision = by_step[step.step_id]
            execution, ctx = self._run_step(step, decision, ordered, outcomes, granted, ctx, audit)
            outcomes[step.step_id] = execution.outcome
            steps.append(execution)

        return ExecutionResult.create(
            plan_id=plan.plan_id, authorization_checksum=authorization.checksum,
            steps=tuple(steps), audit_log=tuple(audit),
            metadata={"invoker": type(self._invoker).__name__})

    def execute_or_raise(self, plan: AgentPlan, authorization: AuthorizationResult, *,
                         approvals: "Iterable[PermissionRequest]" = (),
                         context: ExecutionContext | None = None) -> ExecutionResult:
        """Like :meth:`execute`, but raise ``APPROVAL_MISSING`` up front if any ``APPROVAL_REQUIRED``
        step lacks a granted approval (strict, all-or-nothing on approvals)."""
        self._validate(plan, authorization)
        granted = {r.request_id for r in approvals if r.decision is PermissionDecision.GRANTED}
        missing = [d.tool_id for d in authorization.decisions
                   if d.level is PermissionLevel.APPROVAL_REQUIRED
                   and not (d.request and d.request.request_id in granted)]
        if missing:
            raise ExecutionError(ExecutionErrorCategory.APPROVAL_MISSING,
                                 f"approval missing for: {missing}")
        return self.execute(plan, authorization, approvals=approvals, context=context)

    # ---- validation ------------------------------------------------------
    def _validate(self, plan: AgentPlan, authorization: AuthorizationResult) -> dict[str, Any]:
        if plan is None or plan.version != AGENT_VERSION:
            raise ExecutionError(ExecutionErrorCategory.INVALID_EXECUTION,
                                 "plan missing or unsupported version")
        if authorization is None or authorization.version != PERMISSION_ENGINE_VERSION:
            raise ExecutionError(ExecutionErrorCategory.INVALID_EXECUTION,
                                 "authorization missing or unsupported version")
        if authorization.plan_id != plan.plan_id:
            raise ExecutionError(ExecutionErrorCategory.INVALID_EXECUTION,
                                 "authorization does not match plan")
        by_step = {d.step_id: d for d in authorization.decisions}
        for step in plan.steps:
            if step.step_id not in by_step:
                raise ExecutionError(ExecutionErrorCategory.INVALID_EXECUTION,
                                     f"no authorization decision for step {step.step_id}")
            if not self._registry.has(step.tool_id):
                raise ExecutionError(ExecutionErrorCategory.INVALID_EXECUTION,
                                     f"unknown tool in plan: {step.tool_id}")
        return by_step

    # ---- per-step pipeline ----------------------------------------------
    def _run_step(self, step: Any, decision: Any, ordered: "tuple[Any, ...]",
                  outcomes: dict[str, ExecutionOutcome], granted: set[str],
                  ctx: ExecutionContext,
                  audit: list[AuditEntry]) -> "tuple[StepExecution, ExecutionContext]":
        level = decision.level

        # 1. Own authorization denied -> never execute.
        if level is PermissionLevel.DENIED:
            self._audit(audit, EVENT_DENIED, step.tool_id, ExecutionOutcome.DENIED.value)
            return (self._record(step, level, ExecutionOutcome.DENIED,
                                 reason="authorization denied"), ctx)

        # 2. Dependency blocked -> skip (a dependency did not succeed).
        blocked = [ordered[dep].tool_id for dep in step.depends_on
                   if outcomes.get(ordered[dep].step_id) is not ExecutionOutcome.SUCCESS]
        if blocked:
            self._audit(audit, EVENT_SKIPPED, step.tool_id, ExecutionOutcome.SKIPPED.value)
            return (self._record(step, level, ExecutionOutcome.SKIPPED,
                                 reason=f"dependency not satisfied: {blocked}"), ctx)

        # 3. Approval required but not granted -> skip (approval missing).
        if level is PermissionLevel.APPROVAL_REQUIRED:
            request_id = decision.request.request_id if decision.request else None
            if request_id not in granted:
                self._audit(audit, EVENT_SKIPPED, step.tool_id, ExecutionOutcome.SKIPPED.value)
                return (self._record(step, level, ExecutionOutcome.SKIPPED,
                                     reason="approval not granted",
                                     error_category=ExecutionErrorCategory.APPROVAL_MISSING), ctx)

        # 4. Eligible -> execute through the invoker (registry-gated).
        return self._execute_step(step, level, ctx, audit)

    def _execute_step(self, step: Any, level: PermissionLevel, ctx: ExecutionContext,
                      audit: list[AuditEntry]) -> "tuple[StepExecution, ExecutionContext]":
        tool = self._registry.get(step.tool_id)
        if tool.availability is not ToolAvailability.AVAILABLE:
            self._audit(audit, EVENT_FAILED, step.tool_id, ExecutionOutcome.FAILED.value)
            return (self._record(step, level, ExecutionOutcome.FAILED,
                                 reason=f"tool {tool.tool_id} is {tool.availability.value}",
                                 error_category=ExecutionErrorCategory.TOOL_UNAVAILABLE), ctx)

        call = ToolCall.create(tool_id=step.tool_id, parameters=dict(step.expected_inputs),
                               state=ExecutionState.RUNNING, call_index=step.sequence)
        self._audit(audit, EVENT_STARTED, step.tool_id, ExecutionState.RUNNING.value)
        try:
            result = self._invoker.invoke(tool, call, ctx)
        except Exception as exc:  # noqa: BLE001 - any invoker fault is a deterministic TOOL_FAILURE
            self._audit(audit, EVENT_FAILED, step.tool_id, ExecutionOutcome.FAILED.value)
            failed = ToolResult.create(call_id=call.call_id, state=ExecutionState.FAILED,
                                       success=False, error=str(exc))
            return (self._record(step, level, ExecutionOutcome.FAILED, reason=str(exc),
                                 error_category=ExecutionErrorCategory.TOOL_FAILURE,
                                 tool_call=call, tool_result=failed), ctx)

        if not result.success or result.state is not ExecutionState.SUCCEEDED:
            self._audit(audit, EVENT_FAILED, step.tool_id, ExecutionOutcome.FAILED.value)
            return (self._record(step, level, ExecutionOutcome.FAILED,
                                 reason=result.error or "tool reported failure",
                                 error_category=ExecutionErrorCategory.TOOL_FAILURE,
                                 tool_call=call, tool_result=result), ctx)

        self._audit(audit, EVENT_COMPLETED, step.tool_id, ExecutionOutcome.SUCCESS.value)
        ctx = ctx.with_output(step.tool_id, result.output)
        return (self._record(step, level, ExecutionOutcome.SUCCESS, tool_call=call,
                             tool_result=result), ctx)

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _record(step: Any, level: PermissionLevel, outcome: ExecutionOutcome, *,
                reason: str | None = None,
                error_category: ExecutionErrorCategory | None = None,
                tool_call: ToolCall | None = None,
                tool_result: ToolResult | None = None) -> StepExecution:
        return StepExecution(step_id=step.step_id, sequence=step.sequence, tool_id=step.tool_id,
                             outcome=outcome, level=level, reason=reason,
                             error_category=error_category, tool_call=tool_call,
                             tool_result=tool_result)

    @staticmethod
    def _audit(audit: list[AuditEntry], event: str, tool: str, outcome: str) -> None:
        audit.append(AuditEntry.create(sequence=len(audit), actor="executor", event=event,
                                       tool=tool, outcome=outcome))
