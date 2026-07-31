"""Agent Engine REST API (`/agent/*`, Sprint 7 · Milestone 7).

A **thin transport** over the completed Agent Engine (M1–M6). It owns transport only — it performs
**no** planning, authorization, execution, or provider logic: it validates a request, delegates to
the corresponding component (Planner · Permission Engine · Executor · LLM Planning Adapter), and
serialises the component's deterministic result. Every component error is normalised to an HTTP
status; no internal exception leaks. It imports **no** engine (neither the Prediction nor the Outcome
engine) and adds **no** persistence — agent sessions live in-memory on ``app.state`` for the process.

Components are built once and cached on ``app.state.agent_pipeline`` (the registry/planner/permission
engine/executor/adapter are stateless; only the session map holds state). The pipeline is
artifact-passing: ``/agent/plan`` returns a plan, ``/agent/authorize`` consumes that plan and returns
an authorization, ``/agent/execute`` consumes both — keeping each route a pure transformation.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.agent.executor import EXECUTOR_VERSION, ExecutionError, ExecutionErrorCategory, Executor
from app.agent.models import (
    AGENT_VERSION,
    Agent,
    AgentPlan,
    AgentSession,
    AgentState,
    AgentTask,
    PermissionDecision,
)
from app.agent.permissions import (
    PERMISSION_ENGINE_VERSION,
    AuthorizationResult,
    PermissionEngine,
    PermissionEngineError,
    PermissionErrorCategory,
    PermissionPolicy,
)
from app.agent.planner import PLANNER_VERSION, Planner, PlannerError, PlannerErrorCategory
from app.agent.planning_llm import (
    PLANNING_ADAPTER_VERSION,
    InvalidPlanningRequestError,
    PlanningRequest,
    create_planning_adapter,
)
from app.agent.tools import TOOL_REGISTRY_VERSION, default_registry
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

#: The Agent Engine REST API version.
AGENT_API_VERSION: str = "agent-api-1"

#: Planner error category → HTTP status (normalised transport error).
_HTTP_FROM_PLANNER = {
    PlannerErrorCategory.UNSUPPORTED_TASK: 422, PlannerErrorCategory.TOOL_NOT_FOUND: 404,
    PlannerErrorCategory.TOOL_UNAVAILABLE: 409, PlannerErrorCategory.INVALID_PLAN: 422,
    PlannerErrorCategory.DEPENDENCY_ERROR: 422,
}
#: Permission error category → HTTP status.
_HTTP_FROM_PERMISSION = {
    PermissionErrorCategory.POLICY_ERROR: 422, PermissionErrorCategory.INVALID_PERMISSION: 400,
    PermissionErrorCategory.APPROVAL_REQUIRED: 403, PermissionErrorCategory.PERMISSION_DENIED: 403,
}
#: Execution error category → HTTP status.
_HTTP_FROM_EXECUTION = {
    ExecutionErrorCategory.INVALID_EXECUTION: 422, ExecutionErrorCategory.APPROVAL_MISSING: 403,
    ExecutionErrorCategory.TOOL_UNAVAILABLE: 409, ExecutionErrorCategory.TOOL_FAILURE: 502,
    ExecutionErrorCategory.EXECUTION_ERROR: 500,
}


# --------------------------------------------------------------------------- schemas
class TaskBody(BaseModel):
    description: str
    metadata: dict[str, Any] = {}


class SessionCreateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    agent_id: str | None = None
    task: TaskBody | None = None


class PlanRequest(BaseModel):
    task: TaskBody
    session_id: str | None = None


class AuthorizeRequest(BaseModel):
    plan: dict[str, Any]
    policy: dict[str, Any] | None = None


class ExecuteRequest(BaseModel):
    plan: dict[str, Any]
    authorization: dict[str, Any]
    granted_request_ids: list[str] = []


class SuggestRequest(BaseModel):
    task: TaskBody
    available_tools: list[str] | None = None
    model: str = "stub-planner"
    temperature: float = 0.0
    max_tokens: int = 512
    planning_context: dict[str, Any] = {}
    constraints: dict[str, Any] = {}


# --------------------------------------------------------------------------- helpers
def _versions() -> dict[str, str]:
    return {"agent_version": AGENT_VERSION, "tool_registry_version": TOOL_REGISTRY_VERSION,
            "planner_version": PLANNER_VERSION, "permission_engine_version": PERMISSION_ENGINE_VERSION,
            "executor_version": EXECUTOR_VERSION, "planning_adapter_version": PLANNING_ADAPTER_VERSION,
            "api_version": AGENT_API_VERSION}


@contextmanager
def _observe(endpoint: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
        logger.info("agent api %s ok in %.1fms", endpoint, (time.perf_counter() - start) * 1000)
    except HTTPException as exc:
        logger.info("agent api %s -> %d in %.1fms", endpoint, exc.status_code,
                    (time.perf_counter() - start) * 1000)
        raise


def _pipeline(request: Request) -> dict[str, Any]:
    """Get-or-build the cached agent pipeline on ``app.state``. Coordination only — the components do
    the work. The registry/planner/permission-engine/executor/adapter are stateless; ``sessions`` is
    the only mutable state (in-memory, no persistence)."""
    state = request.app.state
    pipe = getattr(state, "agent_pipeline", None)
    if pipe is None:
        registry = default_registry()
        pipe = {
            "registry": registry,
            "planner": Planner(registry),
            "permission_engine": PermissionEngine(registry),
            "executor": Executor(registry),
            "adapter": getattr(state, "agent_planning_adapter", None) or create_planning_adapter("echo"),
            "sessions": {},
        }
        state.agent_pipeline = pipe
    return pipe


def _rebuild_plan(payload: dict[str, Any]) -> AgentPlan:
    try:
        return AgentPlan.from_dict(payload)
    except Exception as exc:                             # noqa: BLE001 — normalise, never leak
        raise HTTPException(status_code=400, detail=f"malformed plan: {exc}") from exc


def _rebuild_authorization(payload: dict[str, Any]) -> AuthorizationResult:
    try:
        return AuthorizationResult.from_dict(payload)
    except Exception as exc:                             # noqa: BLE001 — normalise, never leak
        raise HTTPException(status_code=400, detail=f"malformed authorization: {exc}") from exc


# --------------------------------------------------------------------------- session endpoints
@router.post("/session")
async def create_session(body: SessionCreateRequest, request: Request) -> dict[str, Any]:
    """Create a new in-memory agent session (transport-managed lifecycle)."""
    with _observe("POST /agent/session"):
        pipe = _pipeline(request)
        agent = Agent.create(name=body.name or "aegis-agent", description=body.description or "",
                             allowed_tools=pipe["registry"].ids())
        task = AgentTask.create(description=body.task.description,
                                metadata=body.task.metadata or None) if body.task else None
        session = AgentSession.create(agent_id=body.agent_id or agent.agent_id, task=task)
        pipe["sessions"][session.session_id] = session
        return {"session": session.to_dict(), "versions": _versions()}


@router.get("/session/{session_id}")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    """Return a stored agent session, or 404."""
    with _observe("GET /agent/session/{id}"):
        session = _pipeline(request)["sessions"].get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")
        return {"session": session.to_dict(), "versions": _versions()}


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    """Cancel (terminal) and drop a session from the in-memory store, or 404."""
    with _observe("DELETE /agent/session/{id}"):
        sessions = _pipeline(request)["sessions"]
        session = sessions.pop(session_id, None)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")
        if AgentState.CANCELLED in _allowed_next(session.state):
            session = session.transition(AgentState.CANCELLED)
        return {"session": session.to_dict(), "deleted": True, "versions": _versions()}


def _allowed_next(state: AgentState) -> set[AgentState]:
    from app.agent.models import _ALLOWED
    return _ALLOWED.get(state, set())


# --------------------------------------------------------------------------- pipeline endpoints
@router.post("/plan")
async def plan(body: PlanRequest, request: Request) -> dict[str, Any]:
    """Plan a task into an AgentPlan (delegates to the Planner; normalises planner errors)."""
    with _observe("POST /agent/plan"):
        if not body.task.description.strip() and not body.task.metadata:
            raise HTTPException(status_code=400, detail="task description or metadata is required")
        pipe = _pipeline(request)
        task = AgentTask.create(description=body.task.description, metadata=body.task.metadata or None)
        try:
            result = pipe["planner"].plan_or_raise(task)
        except PlannerError as exc:
            raise HTTPException(status_code=_HTTP_FROM_PLANNER.get(exc.category, 422),
                                detail={"error": exc.category.value, "message": exc.message})
        _maybe_attach_plan(pipe, body.session_id, result.plan)
        return {"planning_result": result.to_dict(), "plan": result.plan.to_dict(),
                "versions": _versions()}


@router.post("/authorize")
async def authorize(body: AuthorizeRequest, request: Request) -> dict[str, Any]:
    """Authorize a plan against a policy (delegates to the Permission Engine)."""
    with _observe("POST /agent/authorize"):
        pipe = _pipeline(request)
        plan_obj = _rebuild_plan(body.plan)
        engine = pipe["permission_engine"]
        if body.policy is not None:
            try:
                engine = PermissionEngine(pipe["registry"], PermissionPolicy.from_dict(body.policy))
            except PermissionEngineError as exc:
                raise HTTPException(status_code=_HTTP_FROM_PERMISSION.get(exc.category, 400),
                                    detail={"error": exc.category.value, "message": exc.message})
        try:
            authorization = engine.evaluate(plan_obj)
        except PermissionEngineError as exc:
            raise HTTPException(status_code=_HTTP_FROM_PERMISSION.get(exc.category, 400),
                                detail={"error": exc.category.value, "message": exc.message})
        return {"authorization": authorization.to_dict(), "versions": _versions()}


@router.post("/execute")
async def execute(body: ExecuteRequest, request: Request) -> dict[str, Any]:
    """Execute an authorized plan (delegates to the Executor). Approvals are the granted request ids
    among the authorization's decisions."""
    with _observe("POST /agent/execute"):
        pipe = _pipeline(request)
        plan_obj = _rebuild_plan(body.plan)
        authorization = _rebuild_authorization(body.authorization)
        granted = set(body.granted_request_ids)
        approvals = tuple(d.request.decide(PermissionDecision.GRANTED)
                          for d in authorization.decisions
                          if d.request is not None and d.request.request_id in granted)
        try:
            result = pipe["executor"].execute(plan_obj, authorization, approvals=approvals)
        except ExecutionError as exc:
            raise HTTPException(status_code=_HTTP_FROM_EXECUTION.get(exc.category, 500),
                                detail={"error": exc.category.value, "message": exc.message})
        return {"execution_result": result.to_dict(), "versions": _versions()}


@router.post("/suggest")
async def suggest(body: SuggestRequest, request: Request) -> dict[str, Any]:
    """Advisory LLM planning suggestion (delegates to the Planning Adapter). The suggestion is NOT an
    executable plan — the Planner remains the authority. Provider faults arrive normalised in-body."""
    with _observe("POST /agent/suggest"):
        pipe = _pipeline(request)
        tools = tuple(body.available_tools) if body.available_tools is not None else pipe["registry"].ids()
        task = AgentTask.create(description=body.task.description, metadata=body.task.metadata or None)
        try:
            planning_request = PlanningRequest.create(
                task=task, available_tools=tools, planning_context=body.planning_context,
                constraints=body.constraints, model=body.model, temperature=body.temperature,
                max_tokens=body.max_tokens)
        except InvalidPlanningRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        response = pipe["adapter"].suggest(planning_request)
        return {"suggestion": response.to_dict(), "versions": _versions()}


# --------------------------------------------------------------------------- health / version
@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Aggregate readiness of the agent components (no business logic executed)."""
    with _observe("GET /agent/health"):
        pipe = _pipeline(request)
        components = {
            "tool_registry": {"ready": True, "tools": len(pipe["registry"])},
            "planner": {"ready": True},
            "permission_engine": {"ready": True},
            "executor": {"ready": True},
            "planning_adapter": pipe["adapter"].health(),
        }
        return {"status": "ready", "components": components, "versions": _versions()}


@router.get("/version")
async def version() -> dict[str, Any]:
    """The agent-stack versions."""
    with _observe("GET /agent/version"):
        return {"api_version": AGENT_API_VERSION, "versions": _versions()}


def _maybe_attach_plan(pipe: dict[str, Any], session_id: str | None, plan_obj: AgentPlan) -> None:
    """Best-effort: attach the plan to a stored CREATED session and advance it to PLANNING."""
    if not session_id:
        return
    session = pipe["sessions"].get(session_id)
    if session is None or session.state is not AgentState.CREATED:
        return
    pipe["sessions"][session_id] = session.with_plan(plan_obj).transition(AgentState.PLANNING)
