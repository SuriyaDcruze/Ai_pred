"""Deterministic Planner for the Agent Engine (Sprint 7 · Milestone 3).

The Planner turns an :class:`~app.agent.models.AgentTask` into an
:class:`~app.agent.models.AgentPlan` using **only** the metadata in the Tool Registry (M2). It
performs **no** tool execution, permission checks, engine invocation, or LLM calls, and consults no
external system. Planning is a pure function of ``(task, registry, rules, context)``: the same inputs
always yield the same plan (and the same checksum).

Selection is rule-based — a deterministic goal → ordered tool-steps mapping — resolved from the
task's ``metadata['goal']``, an explicit ``metadata['requested_tools']`` list, or a deterministic
keyword scan of the description. Tools are validated against the registry (existence + availability),
inter-tool dependencies are resolved by a deterministic layered topological sort (cycle-detecting),
and the result is a fully ordered, validated ``AgentPlan``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from app.agent.models import (
    AgentPlan,
    AgentTask,
    ExecutionStep,
    InvalidPlanError,
    _checksum,
    _get,
    _utc_now_iso,
)
from app.agent.tools import ToolAvailability, ToolDefinition, ToolRegistry

#: The Planner method/schema version. A shape/method change is a new version, never an edit.
PLANNER_VERSION: str = "plan-1"


# --------------------------------------------------------------------------- enums
class PlanningStatus(str, Enum):
    """The outcome of a planning attempt."""

    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class PlannerErrorCategory(str, Enum):
    """Deterministic planner error categories (the model-level taxonomy)."""

    UNSUPPORTED_TASK = "UNSUPPORTED_TASK"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    INVALID_PLAN = "INVALID_PLAN"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


# --------------------------------------------------------------------------- error
class PlannerError(Exception):
    """A deterministic planning failure carrying a :class:`PlannerErrorCategory`."""

    def __init__(self, category: PlannerErrorCategory, message: str) -> None:
        super().__init__(f"{category.value}: {message}")
        self.category = category
        self.message = message


# --------------------------------------------------------------------------- planning rules
@dataclass(frozen=True)
class PlanStepSpec:
    """One step of a planning rule — a target tool and the other tool ids (in the same plan) it
    depends on. Expected inputs/outputs default to the tool's registry schema when omitted."""

    tool_id: str
    depends_on: tuple[str, ...] = ()
    expected_inputs: dict[str, Any] | None = None
    expected_outputs: dict[str, Any] | None = None

    @classmethod
    def of(cls, tool_id: str, depends_on: "Iterable[str]" = (),
           expected_inputs: dict[str, Any] | None = None,
           expected_outputs: dict[str, Any] | None = None) -> "PlanStepSpec":
        return cls(tool_id=tool_id, depends_on=tuple(depends_on),
                   expected_inputs=expected_inputs, expected_outputs=expected_outputs)


@dataclass(frozen=True)
class PlanningRule:
    """A deterministic goal → ordered tool-steps mapping. ``keywords`` drive the fallback description
    scan (lowercased substring match)."""

    goal: str
    steps: tuple[PlanStepSpec, ...]
    keywords: tuple[str, ...] = ()
    description: str = ""


#: The canonical read-only planning rules over the default tool catalog. Each references tools by the
#: ids used in :func:`app.agent.tools.default_tool_definitions`.
DEFAULT_PLANNING_RULES: tuple[PlanningRule, ...] = (
    PlanningRule(
        goal="explain_prediction",
        keywords=("explain", "why"),
        description="Explain a prediction from its decision intelligence, history, and similar cases.",
        steps=(
            PlanStepSpec.of("decision_intelligence.get"),
            PlanStepSpec.of("memory.get_history"),
            PlanStepSpec.of("similarity.find_similar"),
            PlanStepSpec.of("conversation.explain",
                            depends_on=("decision_intelligence.get", "memory.get_history",
                                        "similarity.find_similar")),
        ),
    ),
    PlanningRule(
        goal="decision_summary", keywords=("decision", "intelligence"),
        description="Fetch the composed decision-intelligence object for a prediction.",
        steps=(PlanStepSpec.of("decision_intelligence.get"),)),
    PlanningRule(
        goal="similar_predictions", keywords=("similar",),
        description="Find historically similar predictions.",
        steps=(PlanStepSpec.of("similarity.find_similar"),)),
    PlanningRule(
        goal="learning_summary", keywords=("learning", "performance"),
        description="Retrieve the deterministic learning summary.",
        steps=(PlanStepSpec.of("learning.get_summary"),)),
    PlanningRule(
        goal="history_lookup", keywords=("history", "past"),
        description="Retrieve historical prediction outcomes.",
        steps=(PlanStepSpec.of("memory.get_history"),)),
    PlanningRule(
        goal="system_status", keywords=("status", "health"),
        description="Report aggregate read-only system health and version.",
        steps=(PlanStepSpec.of("system.health"), PlanStepSpec.of("system.version"))),
)


# --------------------------------------------------------------------------- planning result
@dataclass(frozen=True)
class PlanningResult:
    """The deterministic outcome of a planning attempt — the plan on success, or a categorised error.
    ``checksum`` fingerprints the stable content (``created_at`` excluded)."""

    task_id: str
    status: PlanningStatus
    goal: str | None
    plan: AgentPlan | None
    selected_tools: tuple[str, ...]
    error_category: PlannerErrorCategory | None
    error_message: str | None
    version: str
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    @property
    def ok(self) -> bool:
        return self.status is PlanningStatus.SUCCESS

    @classmethod
    def success(cls, *, task_id: str, goal: str, plan: AgentPlan, selected_tools: tuple[str, ...],
                metadata: dict[str, Any] | None = None) -> "PlanningResult":
        meta = metadata or {}
        return cls(task_id=task_id, status=PlanningStatus.SUCCESS, goal=goal, plan=plan,
                   selected_tools=selected_tools, error_category=None, error_message=None,
                   version=PLANNER_VERSION, metadata=meta,
                   checksum=_result_checksum(task_id, PlanningStatus.SUCCESS, goal, plan,
                                             selected_tools, None, meta))

    @classmethod
    def failure(cls, *, task_id: str, category: PlannerErrorCategory, message: str,
                goal: str | None = None, metadata: dict[str, Any] | None = None) -> "PlanningResult":
        meta = metadata or {}
        return cls(task_id=task_id, status=PlanningStatus.ERROR, goal=goal, plan=None,
                   selected_tools=(), error_category=category, error_message=message,
                   version=PLANNER_VERSION, metadata=meta,
                   checksum=_result_checksum(task_id, PlanningStatus.ERROR, goal, None, (), category,
                                             meta))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "status": self.status.value, "goal": self.goal,
            "plan": self.plan.stable_dict() if self.plan else None,
            "selected_tools": list(self.selected_tools),
            "error_category": self.error_category.value if self.error_category else None,
            "error_message": self.error_message, "version": self.version, "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "plan": self.plan.to_dict() if self.plan else None,
                "checksum": self.checksum, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanningResult":
        plan = _get(data, "plan")
        category = _get(data, "error_category")
        return cls(
            task_id=_get(data, "task_id"),
            status=PlanningStatus(_get(data, "status", PlanningStatus.ERROR.value)),
            goal=_get(data, "goal"), plan=AgentPlan.from_dict(plan) if plan else None,
            selected_tools=tuple(_get(data, "selected_tools") or ()),
            error_category=PlannerErrorCategory(category) if category else None,
            error_message=_get(data, "error_message"),
            version=_get(data, "version", PLANNER_VERSION),
            checksum=_get(data, "checksum", ""), metadata=dict(_get(data, "metadata") or {}),
            created_at=_get(data, "created_at"))


def _result_checksum(task_id: str, status: PlanningStatus, goal: str | None, plan: AgentPlan | None,
                     selected_tools: tuple[str, ...], category: PlannerErrorCategory | None,
                     metadata: dict[str, Any]) -> str:
    return _checksum({
        "task_id": task_id, "status": status.value, "goal": goal,
        "plan": plan.stable_dict() if plan else None, "selected_tools": list(selected_tools),
        "error_category": category.value if category else None, "version": PLANNER_VERSION,
        "metadata": metadata,
    })


# --------------------------------------------------------------------------- planner
class Planner:
    """A deterministic, stateless planner over a :class:`ToolRegistry` and a set of
    :class:`PlanningRule`. No external systems are consulted; no tools are executed."""

    version: str = PLANNER_VERSION

    def __init__(self, registry: ToolRegistry,
                 rules: "Iterable[PlanningRule] | None" = None) -> None:
        self._registry = registry
        rules = tuple(rules) if rules is not None else DEFAULT_PLANNING_RULES
        self._rules: dict[str, PlanningRule] = {}
        for rule in rules:
            if rule.goal in self._rules:
                raise PlannerError(PlannerErrorCategory.INVALID_PLAN,
                                   f"duplicate planning rule for goal {rule.goal!r}")
            self._rules[rule.goal] = rule

    # ---- public API ------------------------------------------------------
    def plan(self, task: AgentTask, context: Mapping[str, Any] | None = None) -> PlanningResult:
        """Plan ``task`` into a :class:`PlanningResult`, capturing any planner error deterministically
        (never raises for a planning failure)."""
        try:
            return self.plan_or_raise(task, context)
        except PlannerError as exc:
            return PlanningResult.failure(task_id=task.task_id, category=exc.category,
                                          message=exc.message, goal=self._safe_goal(task))

    def plan_or_raise(self, task: AgentTask, context: Mapping[str, Any] | None = None) -> PlanningResult:
        """Plan ``task``, raising :class:`PlannerError` (with a category) on any planning failure."""
        goal, specs = self._select(task)
        tools = self._resolve_tools(specs)
        ordered_ids = _topological_order(specs)
        steps = self._build_steps(ordered_ids, specs, tools)
        try:
            plan = AgentPlan.create(steps=steps, metadata={
                "goal": goal, "planner_version": PLANNER_VERSION,
                "context_keys": sorted((context or {}).keys())})
        except InvalidPlanError as exc:  # pragma: no cover - guarded upstream, kept deterministic
            raise PlannerError(PlannerErrorCategory.INVALID_PLAN, str(exc)) from exc
        return PlanningResult.success(task_id=task.task_id, goal=goal, plan=plan,
                                      selected_tools=tuple(ordered_ids),
                                      metadata={"context_keys": sorted((context or {}).keys())})

    # ---- selection -------------------------------------------------------
    def _select(self, task: AgentTask) -> tuple[str, dict[str, PlanStepSpec]]:
        requested = task.metadata.get("requested_tools")
        if requested:
            goal = str(task.metadata.get("goal") or "custom")
            deps = task.metadata.get("dependencies") or {}
            specs = self._specs_from_ids(requested, deps)
            return goal, specs
        goal = self._resolve_goal(task)
        rule = self._rules[goal]
        return goal, self._specs_from_rule(rule)

    def _resolve_goal(self, task: AgentTask) -> str:
        explicit = task.metadata.get("goal")
        if explicit:
            if explicit not in self._rules:
                raise PlannerError(PlannerErrorCategory.UNSUPPORTED_TASK,
                                   f"no planning rule for goal {explicit!r}")
            return str(explicit)
        text = (task.description or "").lower()
        matched = sorted({rule.goal for rule in self._rules.values()
                          if any(kw in text for kw in rule.keywords)})
        if not matched:
            raise PlannerError(PlannerErrorCategory.UNSUPPORTED_TASK,
                               "task does not match any planning rule")
        if len(matched) > 1:
            raise PlannerError(PlannerErrorCategory.UNSUPPORTED_TASK,
                               f"ambiguous task matches multiple goals: {matched}")
        return matched[0]

    def _specs_from_rule(self, rule: PlanningRule) -> dict[str, PlanStepSpec]:
        if not rule.steps:
            raise PlannerError(PlannerErrorCategory.INVALID_PLAN,
                               f"planning rule {rule.goal!r} has no steps")
        specs: dict[str, PlanStepSpec] = {}
        for spec in rule.steps:
            if spec.tool_id in specs:
                raise PlannerError(PlannerErrorCategory.INVALID_PLAN,
                                   f"duplicate step for tool {spec.tool_id!r}")
            specs[spec.tool_id] = spec
        return specs

    def _specs_from_ids(self, tool_ids: "Iterable[str]",
                        dependencies: Mapping[str, Iterable[str]]) -> dict[str, PlanStepSpec]:
        specs: dict[str, PlanStepSpec] = {}
        for tid in tool_ids:
            if tid in specs:
                raise PlannerError(PlannerErrorCategory.INVALID_PLAN,
                                   f"duplicate step for tool {tid!r}")
            specs[tid] = PlanStepSpec.of(tid, depends_on=tuple(dependencies.get(tid, ())))
        if not specs:
            raise PlannerError(PlannerErrorCategory.INVALID_PLAN, "no tools requested")
        return specs

    # ---- resolution / validation ----------------------------------------
    def _resolve_tools(self, specs: Mapping[str, PlanStepSpec]) -> dict[str, ToolDefinition]:
        tools: dict[str, ToolDefinition] = {}
        for tool_id in specs:
            if not self._registry.has(tool_id):
                raise PlannerError(PlannerErrorCategory.TOOL_NOT_FOUND,
                                   f"tool not in registry: {tool_id}")
            tool = self._registry.get(tool_id)
            if tool.availability is not ToolAvailability.AVAILABLE:
                raise PlannerError(PlannerErrorCategory.TOOL_UNAVAILABLE,
                                   f"tool {tool_id} is {tool.availability.value}")
            tools[tool_id] = tool
        return tools

    def _build_steps(self, ordered_ids: list[str], specs: Mapping[str, PlanStepSpec],
                     tools: Mapping[str, ToolDefinition]) -> list[ExecutionStep]:
        index = {tid: i for i, tid in enumerate(ordered_ids)}
        steps: list[ExecutionStep] = []
        for tid in ordered_ids:
            spec = specs[tid]
            tool = tools[tid]
            ins = spec.expected_inputs if spec.expected_inputs is not None else \
                {p.name: p.type for p in tool.input_schema.parameters}
            outs = spec.expected_outputs if spec.expected_outputs is not None else \
                {p.name: p.type for p in tool.output_schema.parameters}
            depends_on = tuple(sorted(index[d] for d in spec.depends_on))
            steps.append(ExecutionStep.create(sequence=index[tid], tool_id=tid, expected_inputs=ins,
                                              expected_outputs=outs, depends_on=depends_on))
        return steps

    def _safe_goal(self, task: AgentTask) -> str | None:
        try:
            return self._select(task)[0]
        except PlannerError:
            return task.metadata.get("goal")


# --------------------------------------------------------------------------- topological order
def _topological_order(specs: Mapping[str, PlanStepSpec]) -> list[str]:
    """Deterministic layered topological sort over inter-tool dependencies. Ties within a layer are
    broken by ``tool_id``; a remaining set with no ready node means a cycle."""
    deps: dict[str, set[str]] = {tid: set(spec.depends_on) for tid, spec in specs.items()}
    for tid, requires in deps.items():
        for dep in requires:
            if dep not in specs:
                raise PlannerError(PlannerErrorCategory.DEPENDENCY_ERROR,
                                   f"step {tid!r} depends on unknown tool {dep!r}")
            if dep == tid:
                raise PlannerError(PlannerErrorCategory.DEPENDENCY_ERROR,
                                   f"step {tid!r} depends on itself")
    ordered: list[str] = []
    placed: set[str] = set()
    remaining = dict(deps)
    while remaining:
        ready = sorted(tid for tid, requires in remaining.items() if requires <= placed)
        if not ready:
            raise PlannerError(PlannerErrorCategory.DEPENDENCY_ERROR,
                               f"circular dependency among {sorted(remaining)}")
        for tid in ready:
            ordered.append(tid)
            placed.add(tid)
            del remaining[tid]
    return ordered
