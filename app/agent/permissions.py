"""Deterministic Permission Engine for the Agent Engine (Sprint 7 · Milestone 4).

The Permission Engine evaluates an :class:`~app.agent.models.AgentPlan` against a policy and decides,
per execution step, whether the tool is ``ALLOWED``, ``APPROVAL_REQUIRED``, or ``DENIED`` — using
**only** the tool metadata in the Tool Registry (M2). It performs **no** tool execution, engine
invocation, planning, LLM calls, state mutation, or persistence, and it never auto-approves anything.

Authorization is deterministic and policy-driven: the same ``(plan, policy, registry)`` always yields
the same result and checksum. A **safety floor** derived from tool metadata is applied after policy
evaluation — a state-changing tool (``WRITE`` capability or ``permission_required``) can never be
relaxed below ``APPROVAL_REQUIRED``; policy may only make a decision *stricter*, never looser.
Steps that require approval get a deterministic :class:`~app.agent.models.PermissionRequest` (left
``PENDING`` — the engine does not decide it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from app.agent.models import (
    AgentPlan,
    PermissionRequest,
    _checksum,
    _get,
    _id,
    _utc_now_iso,
)
from app.agent.tools import ToolCapability, ToolCategory, ToolDefinition, ToolRegistry

#: The Permission Engine method/schema version. A shape/method change is a new version, never an edit.
PERMISSION_ENGINE_VERSION: str = "perm-1"


# --------------------------------------------------------------------------- enums
class PermissionLevel(str, Enum):
    """A deterministic authorization level (also used as an aggregate's overall status)."""

    ALLOWED = "ALLOWED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DENIED = "DENIED"


#: Severity order — a strictly-increasing scale used to take the *strictest* of two levels.
_SEVERITY: dict[PermissionLevel, int] = {
    PermissionLevel.ALLOWED: 0,
    PermissionLevel.APPROVAL_REQUIRED: 1,
    PermissionLevel.DENIED: 2,
}


def _strictest(a: PermissionLevel, b: PermissionLevel) -> PermissionLevel:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


class PermissionErrorCategory(str, Enum):
    """Deterministic permission error categories (the model-level taxonomy)."""

    POLICY_ERROR = "POLICY_ERROR"
    INVALID_PERMISSION = "INVALID_PERMISSION"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"


# --------------------------------------------------------------------------- error
class PermissionEngineError(Exception):
    """A deterministic permission failure carrying a :class:`PermissionErrorCategory`."""

    def __init__(self, category: PermissionErrorCategory, message: str) -> None:
        super().__init__(f"{category.value}: {message}")
        self.category = category
        self.message = message


# --------------------------------------------------------------------------- policy
@dataclass(frozen=True)
class PermissionRule:
    """One deterministic policy rule. A rule matches a tool when every *specified* matcher
    (``tool_id`` / ``category`` / ``capability``) matches; an all-``None`` rule is a catch-all. Rules
    are evaluated in order and the first match wins."""

    rule_id: str
    level: PermissionLevel
    tool_id: str | None = None
    category: ToolCategory | None = None
    capability: ToolCapability | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.level, PermissionLevel):
            raise PermissionEngineError(PermissionErrorCategory.INVALID_PERMISSION,
                                        f"invalid permission level {self.level!r}")
        if self.category is not None and not isinstance(self.category, ToolCategory):
            raise PermissionEngineError(PermissionErrorCategory.INVALID_PERMISSION,
                                        f"invalid category {self.category!r}")
        if self.capability is not None and not isinstance(self.capability, ToolCapability):
            raise PermissionEngineError(PermissionErrorCategory.INVALID_PERMISSION,
                                        f"invalid capability {self.capability!r}")

    @classmethod
    def create(cls, *, level: PermissionLevel, tool_id: str | None = None,
               category: ToolCategory | None = None, capability: ToolCapability | None = None,
               reason: str | None = None) -> "PermissionRule":
        rule_id = _id("permrule", level.value, tool_id, category.value if category else None,
                      capability.value if capability else None)
        return cls(rule_id=rule_id, level=level, tool_id=tool_id, category=category,
                   capability=capability, reason=reason)

    def matches(self, tool: ToolDefinition) -> bool:
        if self.tool_id is not None and self.tool_id != tool.tool_id:
            return False
        if self.category is not None and self.category is not tool.category:
            return False
        if self.capability is not None and self.capability is not tool.capability:
            return False
        return True

    def stable_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "level": self.level.value, "tool_id": self.tool_id,
                "category": self.category.value if self.category else None,
                "capability": self.capability.value if self.capability else None,
                "reason": self.reason}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PermissionRule":
        category = _get(data, "category")
        capability = _get(data, "capability")
        return cls(rule_id=_get(data, "rule_id"),
                   level=PermissionLevel(_get(data, "level", PermissionLevel.ALLOWED.value)),
                   tool_id=_get(data, "tool_id"),
                   category=ToolCategory(category) if category else None,
                   capability=ToolCapability(capability) if capability else None,
                   reason=_get(data, "reason"))


def _policy_checksum(version: str, name: str, default_level: PermissionLevel,
                     rules: "tuple[PermissionRule, ...]", metadata: dict[str, Any]) -> str:
    return _checksum({"version": version, "name": name, "default_level": default_level.value,
                      "rules": [r.stable_dict() for r in rules], "metadata": metadata})


@dataclass(frozen=True)
class PermissionPolicy:
    """An ordered, deterministic set of :class:`PermissionRule` with a default level. First matching
    rule wins; a non-matching step falls back to ``default_level``. Immutable + checksummed. The
    policy can only *tighten* the metadata-derived safety floor — never loosen it."""

    version: str = PERMISSION_ENGINE_VERSION
    name: str = "default"
    default_level: PermissionLevel = PermissionLevel.ALLOWED
    rules: tuple[PermissionRule, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""

    def __post_init__(self) -> None:
        if self.version != PERMISSION_ENGINE_VERSION:
            raise PermissionEngineError(PermissionErrorCategory.POLICY_ERROR,
                                        f"unsupported policy version {self.version!r}")
        if not isinstance(self.default_level, PermissionLevel):
            raise PermissionEngineError(PermissionErrorCategory.INVALID_PERMISSION,
                                        f"invalid default level {self.default_level!r}")
        ids = [r.rule_id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise PermissionEngineError(PermissionErrorCategory.POLICY_ERROR,
                                        f"conflicting (duplicate) rule ids: {ids}")
        object.__setattr__(self, "checksum", _policy_checksum(
            self.version, self.name, self.default_level, self.rules, self.metadata))

    @classmethod
    def create(cls, *, rules: "Iterable[PermissionRule] | None" = None, name: str = "default",
               default_level: PermissionLevel = PermissionLevel.ALLOWED,
               version: str = PERMISSION_ENGINE_VERSION,
               metadata: dict[str, Any] | None = None) -> "PermissionPolicy":
        return cls(version=version, name=name, default_level=default_level,
                   rules=tuple(rules or ()), metadata=metadata or {})

    def resolve(self, tool: ToolDefinition) -> tuple[PermissionLevel, str | None]:
        """Return the (policy_level, matched_rule_id) for ``tool`` — first match wins, else default."""
        for rule in self.rules:
            if rule.matches(tool):
                return rule.level, rule.rule_id
        return self.default_level, None

    def stable_dict(self) -> dict[str, Any]:
        return {"version": self.version, "name": self.name,
                "default_level": self.default_level.value,
                "rules": [r.stable_dict() for r in self.rules], "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PermissionPolicy":
        return cls.create(
            rules=[PermissionRule.from_dict(r) for r in (_get(data, "rules") or [])],
            name=_get(data, "name", "default"),
            default_level=PermissionLevel(_get(data, "default_level", PermissionLevel.ALLOWED.value)),
            version=_get(data, "version", PERMISSION_ENGINE_VERSION),
            metadata=dict(_get(data, "metadata") or {}))


def default_policy() -> PermissionPolicy:
    """The canonical policy: read-only tools are ``ALLOWED``; the metadata safety floor pushes every
    state-changing tool to ``APPROVAL_REQUIRED``. No rule denies anything by default."""
    return PermissionPolicy.create(name="default", default_level=PermissionLevel.ALLOWED)


# --------------------------------------------------------------------------- authorization results
@dataclass(frozen=True)
class StepAuthorization:
    """The deterministic authorization decision for one execution step, including the metadata floor,
    the policy's level, the matched rule, and (when approval is required) a ``PermissionRequest``."""

    step_id: str
    sequence: int
    tool_id: str
    level: PermissionLevel
    floor_level: PermissionLevel
    policy_level: PermissionLevel
    matched_rule_id: str | None
    reason: str | None
    request: PermissionRequest | None = None

    def stable_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "sequence": self.sequence, "tool_id": self.tool_id,
                "level": self.level.value, "floor_level": self.floor_level.value,
                "policy_level": self.policy_level.value, "matched_rule_id": self.matched_rule_id,
                "reason": self.reason,
                "request": self.request.stable_dict() if self.request else None}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(),
                "request": self.request.to_dict() if self.request else None}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepAuthorization":
        request = _get(data, "request")
        return cls(
            step_id=_get(data, "step_id"), sequence=int(_get(data, "sequence", 0)),
            tool_id=_get(data, "tool_id"),
            level=PermissionLevel(_get(data, "level", PermissionLevel.ALLOWED.value)),
            floor_level=PermissionLevel(_get(data, "floor_level", PermissionLevel.ALLOWED.value)),
            policy_level=PermissionLevel(_get(data, "policy_level", PermissionLevel.ALLOWED.value)),
            matched_rule_id=_get(data, "matched_rule_id"), reason=_get(data, "reason"),
            request=PermissionRequest.from_dict(request) if request else None)


def _authorization_checksum(plan_id: str, policy_name: str, policy_checksum: str,
                            overall: PermissionLevel, decisions: "tuple[StepAuthorization, ...]",
                            version: str, metadata: dict[str, Any]) -> str:
    return _checksum({
        "plan_id": plan_id, "policy_name": policy_name, "policy_checksum": policy_checksum,
        "overall": overall.value, "decisions": [d.stable_dict() for d in decisions],
        "version": version, "metadata": metadata,
    })


@dataclass(frozen=True)
class AuthorizationResult:
    """The deterministic per-plan authorization: a decision for every step, an aggregate ``overall``
    status (the strictest step level), and the generated approval requests. Checksummed; round-trip
    serializable."""

    plan_id: str
    policy_name: str
    policy_checksum: str
    overall: PermissionLevel
    decisions: tuple[StepAuthorization, ...]
    version: str
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @property
    def approvals(self) -> tuple[PermissionRequest, ...]:
        """The deterministic approval requests, in step order."""
        return tuple(d.request for d in self.decisions if d.request is not None)

    @property
    def allowed(self) -> bool:
        return self.overall is PermissionLevel.ALLOWED

    @classmethod
    def create(cls, *, plan_id: str, policy: PermissionPolicy,
               decisions: "tuple[StepAuthorization, ...]",
               metadata: dict[str, Any] | None = None) -> "AuthorizationResult":
        overall = PermissionLevel.ALLOWED
        for decision in decisions:
            overall = _strictest(overall, decision.level)
        meta = metadata or {}
        return cls(plan_id=plan_id, policy_name=policy.name, policy_checksum=policy.checksum,
                   overall=overall, decisions=decisions, version=PERMISSION_ENGINE_VERSION,
                   metadata=meta,
                   checksum=_authorization_checksum(plan_id, policy.name, policy.checksum, overall,
                                                    decisions, PERMISSION_ENGINE_VERSION, meta))

    def stable_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "policy_name": self.policy_name,
                "policy_checksum": self.policy_checksum, "overall": self.overall.value,
                "decisions": [d.stable_dict() for d in self.decisions], "version": self.version,
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "decisions": [d.to_dict() for d in self.decisions],
                "checksum": self.checksum, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuthorizationResult":
        return cls(
            plan_id=_get(data, "plan_id"), policy_name=_get(data, "policy_name", "default"),
            policy_checksum=_get(data, "policy_checksum", ""),
            overall=PermissionLevel(_get(data, "overall", PermissionLevel.ALLOWED.value)),
            decisions=tuple(StepAuthorization.from_dict(d) for d in (_get(data, "decisions") or [])),
            version=_get(data, "version", PERMISSION_ENGINE_VERSION),
            checksum=_get(data, "checksum", ""), metadata=dict(_get(data, "metadata") or {}),
            created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- engine
class PermissionEngine:
    """A deterministic, stateless authorization engine over a :class:`PermissionPolicy` and a
    :class:`ToolRegistry`. It reads tool metadata only — it executes nothing and persists nothing."""

    version: str = PERMISSION_ENGINE_VERSION

    def __init__(self, registry: ToolRegistry, policy: PermissionPolicy | None = None) -> None:
        self._registry = registry
        self._policy = policy or default_policy()

    @property
    def policy(self) -> PermissionPolicy:
        return self._policy

    def evaluate(self, plan: AgentPlan) -> AuthorizationResult:
        """Authorize every step of ``plan``; returns a result (never raises for an approval/denied
        outcome — those are captured as levels). Raises only for a policy/metadata fault."""
        decisions = tuple(self._evaluate_step(step) for step in plan.steps)
        return AuthorizationResult.create(plan_id=plan.plan_id, policy=self._policy,
                                          decisions=decisions,
                                          metadata={"policy_name": self._policy.name})

    def authorize_or_raise(self, plan: AgentPlan) -> AuthorizationResult:
        """Evaluate and raise if the plan is not fully ``ALLOWED`` — ``PERMISSION_DENIED`` if any step
        is denied, else ``APPROVAL_REQUIRED`` if any step needs approval."""
        result = self.evaluate(plan)
        if result.overall is PermissionLevel.DENIED:
            denied = [d.tool_id for d in result.decisions if d.level is PermissionLevel.DENIED]
            raise PermissionEngineError(PermissionErrorCategory.PERMISSION_DENIED,
                                        f"denied tools: {denied}")
        if result.overall is PermissionLevel.APPROVAL_REQUIRED:
            pending = [d.tool_id for d in result.decisions
                       if d.level is PermissionLevel.APPROVAL_REQUIRED]
            raise PermissionEngineError(PermissionErrorCategory.APPROVAL_REQUIRED,
                                        f"approval required for: {pending}")
        return result

    # ---- internals -------------------------------------------------------
    def _evaluate_step(self, step: Any) -> StepAuthorization:
        if not self._registry.has(step.tool_id):
            raise PermissionEngineError(PermissionErrorCategory.POLICY_ERROR,
                                        f"cannot authorize unknown tool {step.tool_id!r}")
        tool = self._registry.get(step.tool_id)
        floor = self._floor(tool)
        policy_level, matched_rule_id = self._policy.resolve(tool)
        level = _strictest(policy_level, floor)
        reason = self._reason(tool, level, floor, policy_level)
        request = None
        if level is PermissionLevel.APPROVAL_REQUIRED:
            request = PermissionRequest.create(
                tool_id=tool.tool_id, action="execute", reason=reason,
                metadata={"sequence": step.sequence, "capability": tool.capability.value})
        return StepAuthorization(step_id=step.step_id, sequence=step.sequence, tool_id=tool.tool_id,
                                 level=level, floor_level=floor, policy_level=policy_level,
                                 matched_rule_id=matched_rule_id, reason=reason, request=request)

    @staticmethod
    def _floor(tool: ToolDefinition) -> PermissionLevel:
        """The metadata safety floor: a state-changing tool can never be looser than approval."""
        if tool.capability is ToolCapability.WRITE or tool.permission_required:
            return PermissionLevel.APPROVAL_REQUIRED
        return PermissionLevel.ALLOWED

    @staticmethod
    def _reason(tool: ToolDefinition, level: PermissionLevel, floor: PermissionLevel,
                policy_level: PermissionLevel) -> str | None:
        if level is PermissionLevel.DENIED:
            return f"policy denies {tool.tool_id}"
        if level is PermissionLevel.APPROVAL_REQUIRED:
            if floor is PermissionLevel.APPROVAL_REQUIRED and policy_level is not PermissionLevel.APPROVAL_REQUIRED:
                return (f"{tool.capability.value} / permission-required tool "
                        f"{tool.tool_id} needs explicit approval")
            return f"policy requires approval for {tool.tool_id}"
        return None
