"""Workflow Definition & Validation (Sprint 8 · Milestone 2).

Static, deterministic validation of a declarative :class:`~app.workflow.models.WorkflowDefinition`
plus an immutable :class:`WorkflowRegistry`. **This milestone is metadata + validation only** — it
*describes* and *checks* workflow graphs; it executes nothing, evaluates no branch predicate, invokes
no Agent Engine, schedules nothing, and adds no persistence.

Validation is **structural only** and a pure function of the definition: unique ids, a valid initial
step, transitions that reference existing steps, full reachability from the initial step, a reachable
terminal step, no duplicate/dangling transitions, an **acyclic** graph, and well-formed
retry/timeout/rollback **policy structures** (carried in each step's `config` / the definition's
`metadata` — the M1 model is unchanged) and well-formed referenced agent-task identifiers. It never
evaluates a predicate or a policy — it only checks their shape.

Determinism: a `ValidationResult` and the `WorkflowRegistry` carry SHA-256 checksums over their stable
content; listings are deterministically ordered; everything serialises round-trip. The module imports
**no** engine and **not** the Agent / Conversation / Decision-Intelligence layers — only the M1
domain model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from app.workflow.models import (
    StepKind,
    WorkflowDefinition,
    WorkflowError,
    WorkflowStep,
    WorkflowTransition,
    _checksum,
    _get,
    _id,
)

#: The Definition & Validation method/schema version. A shape/method change is a new version.
WORKFLOW_DEFINITION_VERSION: str = "wfdef-1"

#: Policy keys a step's `config` (or a definition's `metadata`) may carry — validated structurally.
_RETRY_STRATEGIES: frozenset[str] = frozenset({"fixed", "exponential", "linear"})
_ROLLBACK_TRIGGERS: frozenset[str] = frozenset({"failure", "cancel", "timeout"})


# --------------------------------------------------------------------------- error taxonomy
class ValidationErrorCode(str, Enum):
    """The deterministic static-validation error taxonomy."""

    INVALID_WORKFLOW = "INVALID_WORKFLOW"
    DUPLICATE_WORKFLOW = "DUPLICATE_WORKFLOW"
    DUPLICATE_STEP = "DUPLICATE_STEP"
    UNKNOWN_STEP = "UNKNOWN_STEP"
    INVALID_INITIAL_STEP = "INVALID_INITIAL_STEP"
    UNREACHABLE_STEP = "UNREACHABLE_STEP"
    NO_TERMINAL_STEP = "NO_TERMINAL_STEP"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    DUPLICATE_TRANSITION = "DUPLICATE_TRANSITION"
    CYCLIC_GRAPH = "CYCLIC_GRAPH"
    INVALID_POLICY = "INVALID_POLICY"
    INVALID_AGENT_TASK = "INVALID_AGENT_TASK"


class ValidationError(WorkflowError):
    """A single deterministic validation failure carrying a :class:`ValidationErrorCode`."""

    def __init__(self, code: ValidationErrorCode, message: str, subject: str | None = None) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.subject = subject


# --------------------------------------------------------------------------- validation result
@dataclass(frozen=True)
class ValidationIssue:
    """One structural problem found in a definition — its code, a message, and the offending subject."""

    code: ValidationErrorCode
    message: str
    subject: str | None = None

    def stable_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "subject": self.subject}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ValidationIssue":
        return cls(code=ValidationErrorCode(_get(data, "code")), message=_get(data, "message", ""),
                   subject=_get(data, "subject"))


@dataclass(frozen=True)
class ValidationResult:
    """The deterministic outcome of validating one definition — the ordered issues (empty ⇒ valid).
    Checksummed; round-trip serialisable."""

    definition_id: str
    valid: bool
    issues: tuple[ValidationIssue, ...]
    version: str
    checksum: str

    @property
    def codes(self) -> tuple[ValidationErrorCode, ...]:
        return tuple(i.code for i in self.issues)

    @classmethod
    def create(cls, *, definition_id: str, issues: "Iterable[ValidationIssue]",
               version: str = WORKFLOW_DEFINITION_VERSION) -> "ValidationResult":
        ordered = tuple(issues)
        valid = len(ordered) == 0
        checksum = _checksum({"definition_id": definition_id, "valid": valid,
                              "issues": [i.stable_dict() for i in ordered], "version": version})
        return cls(definition_id=definition_id, valid=valid, issues=ordered, version=version,
                   checksum=checksum)

    def raise_if_invalid(self) -> "ValidationResult":
        if not self.valid:
            first = self.issues[0]
            raise ValidationError(first.code, first.message, first.subject)
        return self

    def stable_dict(self) -> dict[str, Any]:
        return {"definition_id": self.definition_id, "valid": self.valid,
                "issues": [i.stable_dict() for i in self.issues], "version": self.version}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ValidationResult":
        return cls.create(
            definition_id=_get(data, "definition_id"),
            issues=[ValidationIssue.from_dict(i) for i in (_get(data, "issues") or [])],
            version=_get(data, "version", WORKFLOW_DEFINITION_VERSION))


# --------------------------------------------------------------------------- validator
class DefinitionValidator:
    """A deterministic, stateless **static** validator for a `WorkflowDefinition`. It collects every
    structural issue (order is deterministic) into a `ValidationResult`. It evaluates **no** branch
    predicate and **no** policy — it checks structure only, and invokes no engine."""

    version: str = WORKFLOW_DEFINITION_VERSION

    def validate(self, definition: WorkflowDefinition) -> ValidationResult:
        issues: list[ValidationIssue] = []
        steps = definition.steps
        step_ids = [s.step_id for s in steps]
        id_set = set(step_ids)

        # 1. workflow-level shape
        if not definition.name:
            issues.append(_issue(ValidationErrorCode.INVALID_WORKFLOW, "workflow name is required"))
        if not steps:
            issues.append(_issue(ValidationErrorCode.INVALID_WORKFLOW, "at least one step is required"))

        # 2. duplicate step ids
        for sid in sorted({s for s in step_ids if step_ids.count(s) > 1}):
            issues.append(_issue(ValidationErrorCode.DUPLICATE_STEP, f"duplicate step id {sid!r}", sid))

        # 3. initial step
        if steps and definition.initial_step not in id_set:
            issues.append(_issue(ValidationErrorCode.INVALID_INITIAL_STEP,
                                 f"initial_step {definition.initial_step!r} is not a known step",
                                 definition.initial_step))

        # 4. transition integrity (dangling / self-loop / from-terminal)
        kinds = {s.step_id: s.kind for s in steps}
        for tr in definition.transitions:
            if tr.from_step not in id_set:
                issues.append(_issue(ValidationErrorCode.UNKNOWN_STEP,
                                     f"transition from unknown step {tr.from_step!r}", tr.from_step))
            if tr.to_step not in id_set:
                issues.append(_issue(ValidationErrorCode.UNKNOWN_STEP,
                                     f"transition to unknown step {tr.to_step!r}", tr.to_step))
            if tr.from_step == tr.to_step:
                issues.append(_issue(ValidationErrorCode.INVALID_TRANSITION,
                                     f"self-loop transition on {tr.from_step!r}", tr.from_step))
            elif kinds.get(tr.from_step) is StepKind.TERMINAL:
                issues.append(_issue(ValidationErrorCode.INVALID_TRANSITION,
                                     f"transition out of terminal step {tr.from_step!r}", tr.from_step))

        # 5. duplicate transitions (same from/to/condition)
        seen: set[tuple[str, str, str | None]] = set()
        for tr in definition.transitions:
            key = (tr.from_step, tr.to_step, tr.condition)
            if key in seen:
                issues.append(_issue(ValidationErrorCode.DUPLICATE_TRANSITION,
                                     f"duplicate transition {tr.from_step}->{tr.to_step}"
                                     f"{'' if tr.condition is None else f' [{tr.condition}]'}",
                                     tr.transition_id))
            seen.add(key)

        # Graph checks operate on the valid subset (known steps only) to stay deterministic.
        adjacency: dict[str, list[str]] = {sid: [] for sid in id_set}
        for tr in definition.transitions:
            if tr.from_step in id_set and tr.to_step in id_set and tr.from_step != tr.to_step:
                adjacency[tr.from_step].append(tr.to_step)
        for sid in adjacency:
            adjacency[sid].sort()

        # 6. acyclicity
        if steps and _has_cycle(adjacency):
            issues.append(_issue(ValidationErrorCode.CYCLIC_GRAPH,
                                 "workflow graph must be acyclic"))

        # 7 & 8. reachability + reachable terminal (only meaningful with a valid initial step)
        if steps and definition.initial_step in id_set:
            reachable = _reachable_from(definition.initial_step, adjacency)
            for sid in sorted(id_set - reachable):
                issues.append(_issue(ValidationErrorCode.UNREACHABLE_STEP,
                                     f"step {sid!r} is not reachable from the initial step", sid))
            terminals = {s.step_id for s in steps
                         if s.kind is StepKind.TERMINAL or not adjacency.get(s.step_id)}
            if not (reachable & terminals):
                issues.append(_issue(ValidationErrorCode.NO_TERMINAL_STEP,
                                     "no terminal step is reachable from the initial step"))

        # 9. policy structure (retry / timeout / rollback) on steps and the definition metadata
        for step in steps:
            issues.extend(_validate_policies(step.config, subject=step.step_id))
            # 10. referenced agent-task identifiers (TASK steps)
            issues.extend(_validate_agent_task(step))
        issues.extend(_validate_policies(definition.metadata, subject=definition.definition_id))

        return ValidationResult.create(definition_id=definition.definition_id, issues=issues,
                                       version=self.version)

    def validate_or_raise(self, definition: WorkflowDefinition) -> ValidationResult:
        """Validate and raise :class:`ValidationError` on the first issue; else return the result."""
        return self.validate(definition).raise_if_invalid()


def _issue(code: ValidationErrorCode, message: str, subject: str | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, subject=subject)


def _reachable_from(start: str, adjacency: Mapping[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adjacency.get(node, ()))
    return seen


def _has_cycle(adjacency: Mapping[str, list[str]]) -> bool:
    WHITE, GREY, BLACK = 0, 1, 2
    color = {node: WHITE for node in adjacency}

    def visit(node: str) -> bool:
        color[node] = GREY
        for nxt in adjacency.get(node, ()):
            if color.get(nxt, WHITE) == GREY:
                return True
            if color.get(nxt, WHITE) == WHITE and visit(nxt):
                return True
        color[node] = BLACK
        return False

    return any(color[node] == WHITE and visit(node) for node in sorted(adjacency))


def _validate_agent_task(step: WorkflowStep) -> list[ValidationIssue]:
    if step.kind is not StepKind.TASK:
        return []
    task = step.agent_task
    if not isinstance(task, str) or not task.strip():
        return [_issue(ValidationErrorCode.INVALID_AGENT_TASK,
                       f"TASK step {step.step_id!r} needs a non-empty agent_task identifier",
                       step.step_id)]
    return []


def _validate_policies(container: Mapping[str, Any], subject: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "retry" in container:
        issues.extend(_validate_retry(container.get("retry"), subject))
    if "timeout" in container:
        issues.extend(_validate_timeout(container.get("timeout"), subject))
    if "rollback" in container:
        issues.extend(_validate_rollback(container.get("rollback"), subject))
    return issues


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_retry(policy: Any, subject: str) -> list[ValidationIssue]:
    if not isinstance(policy, Mapping):
        return [_issue(ValidationErrorCode.INVALID_POLICY, "retry policy must be an object", subject)]
    out: list[ValidationIssue] = []
    attempts = policy.get("max_attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        out.append(_issue(ValidationErrorCode.INVALID_POLICY,
                          "retry.max_attempts must be an integer >= 0", subject))
    if "backoff_seconds" in policy and (not _is_number(policy["backoff_seconds"])
                                        or policy["backoff_seconds"] < 0):
        out.append(_issue(ValidationErrorCode.INVALID_POLICY,
                          "retry.backoff_seconds must be a number >= 0", subject))
    if "strategy" in policy and policy["strategy"] not in _RETRY_STRATEGIES:
        out.append(_issue(ValidationErrorCode.INVALID_POLICY,
                          f"retry.strategy must be one of {sorted(_RETRY_STRATEGIES)}", subject))
    return out


def _validate_timeout(policy: Any, subject: str) -> list[ValidationIssue]:
    if not isinstance(policy, Mapping):
        return [_issue(ValidationErrorCode.INVALID_POLICY, "timeout policy must be an object", subject)]
    seconds = policy.get("seconds")
    if not _is_number(seconds) or seconds <= 0:
        return [_issue(ValidationErrorCode.INVALID_POLICY,
                       "timeout.seconds must be a number > 0", subject)]
    return []


def _validate_rollback(policy: Any, subject: str) -> list[ValidationIssue]:
    if not isinstance(policy, Mapping):
        return [_issue(ValidationErrorCode.INVALID_POLICY, "rollback policy must be an object", subject)]
    out: list[ValidationIssue] = []
    hook = policy.get("hook")
    if not isinstance(hook, str) or not hook.strip():
        out.append(_issue(ValidationErrorCode.INVALID_POLICY,
                          "rollback.hook must be a non-empty string", subject))
    if "on" in policy and policy["on"] not in _ROLLBACK_TRIGGERS:
        out.append(_issue(ValidationErrorCode.INVALID_POLICY,
                          f"rollback.on must be one of {sorted(_ROLLBACK_TRIGGERS)}", subject))
    return out


# --------------------------------------------------------------------------- registry
def _registry_checksum(version: str, definitions: "tuple[WorkflowDefinition, ...]") -> str:
    return _checksum({"version": version, "registry_id": _id("wfregistry", version),
                      "definitions": [d.stable_dict() for d in definitions]})


@dataclass(frozen=True)
class WorkflowRegistry:
    """An **immutable**, deterministic catalog of `WorkflowDefinition`s keyed by `definition_id`.
    Registration is functional — :meth:`register` returns a *new* registry with a recomputed checksum.
    Duplicate ids are rejected (`DUPLICATE_WORKFLOW`); every listing is ordered by `definition_id`.
    Metadata only — it executes nothing."""

    version: str = WORKFLOW_DEFINITION_VERSION
    definitions: tuple[WorkflowDefinition, ...] = ()
    checksum: str = ""

    def __post_init__(self) -> None:
        if self.version != WORKFLOW_DEFINITION_VERSION:
            raise ValidationError(ValidationErrorCode.INVALID_WORKFLOW,
                                  f"unsupported registry version {self.version!r}")
        ids = [d.definition_id for d in self.definitions]
        if len(ids) != len(set(ids)):
            raise ValidationError(ValidationErrorCode.DUPLICATE_WORKFLOW,
                                  f"duplicate workflow ids in registry: {ids}")
        if self.definitions != tuple(sorted(self.definitions, key=lambda d: d.definition_id)):
            raise ValidationError(ValidationErrorCode.INVALID_WORKFLOW,
                                  "registry definitions must be ordered by definition_id")
        object.__setattr__(self, "checksum", _registry_checksum(self.version, self.definitions))

    @classmethod
    def create(cls, definitions: "Iterable[WorkflowDefinition] | None" = None, *,
               version: str = WORKFLOW_DEFINITION_VERSION) -> "WorkflowRegistry":
        registry = cls(version=version, definitions=())
        return registry.register_all(definitions or ())

    def register(self, definition: WorkflowDefinition) -> "WorkflowRegistry":
        """Return a new registry with ``definition`` added. Rejects a conflicting id."""
        if any(d.definition_id == definition.definition_id for d in self.definitions):
            raise ValidationError(ValidationErrorCode.DUPLICATE_WORKFLOW,
                                  f"workflow id already registered: {definition.definition_id}",
                                  definition.definition_id)
        ordered = tuple(sorted(self.definitions + (definition,), key=lambda d: d.definition_id))
        return WorkflowRegistry(version=self.version, definitions=ordered)

    def register_all(self, definitions: "Iterable[WorkflowDefinition]") -> "WorkflowRegistry":
        registry = self
        for definition in definitions:
            registry = registry.register(definition)
        return registry

    def has(self, definition_id: str) -> bool:
        return any(d.definition_id == definition_id for d in self.definitions)

    def get(self, definition_id: str) -> WorkflowDefinition:
        for definition in self.definitions:
            if definition.definition_id == definition_id:
                return definition
        raise ValidationError(ValidationErrorCode.UNKNOWN_STEP,
                              f"workflow not found: {definition_id}", definition_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(d.definition_id for d in self.definitions)

    def list(self) -> tuple[WorkflowDefinition, ...]:
        """Every definition, deterministically ordered by `definition_id`."""
        return self.definitions

    def __len__(self) -> int:
        return len(self.definitions)

    def __contains__(self, definition_id: object) -> bool:
        return isinstance(definition_id, str) and self.has(definition_id)

    def stable_dict(self) -> dict[str, Any]:
        return {"version": self.version, "definitions": [d.stable_dict() for d in self.definitions]}

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "definitions": [d.to_dict() for d in self.definitions],
                "checksum": self.checksum}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorkflowRegistry":
        return cls.create(
            definitions=[WorkflowDefinition.from_dict(d) for d in (_get(data, "definitions") or [])],
            version=_get(data, "version", WORKFLOW_DEFINITION_VERSION))
