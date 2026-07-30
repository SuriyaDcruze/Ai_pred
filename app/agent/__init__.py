"""Agent Engine — deterministic planning + permissioned tool execution over AEGIS (Sprint 7).

The Agent Engine (later milestones) will plan and — under **explicit permission** — execute tool
calls against the existing **read-only** AEGIS engines, with a full immutable audit trail. It never
predicts or advises; it orchestrates existing deterministic capabilities.

**Current state (Milestones 1–2):**
- **M1 — the Agent domain model:** `Agent`, `AgentSession` (+ its validated lifecycle state
  machine), `AgentTask`, `AgentPlan`, `ExecutionStep`, `ToolCall`, `ToolResult`,
  `PermissionRequest`, and the immutable `AuditEntry`.
- **M2 — the Tool Registry (`tool-1`, metadata only):** `ToolDefinition`, `ToolSchema`,
  `ToolParameter`, `ToolRegistry`, the `ToolCategory` / `ToolCapability` / `ToolAvailability` enums,
  and the read-only `default_registry()` catalog.
- **M3 — the deterministic Planner (`plan-1`):** `Planner`, `PlanningResult`, `PlanningRule`,
  `PlanStepSpec`, the `PlanningStatus` / `PlannerErrorCategory` enums, and `DEFAULT_PLANNING_RULES`.
  Turns an `AgentTask` into an `AgentPlan` using only registry metadata — no execution, permissions,
  engine calls, or LLM.
- **M4 — the deterministic Permission Engine (`perm-1`):** `PermissionEngine`, `PermissionPolicy`,
  `PermissionRule`, `AuthorizationResult`, `StepAuthorization`, the `PermissionLevel` /
  `PermissionErrorCategory` enums, and `default_policy()`. Authorizes each plan step
  (ALLOWED / APPROVAL_REQUIRED / DENIED) from tool metadata + policy, emitting `PermissionRequest`s —
  no execution, engine calls, state mutation, or LLM.
- **M5 — the deterministic Executor (`exec-1`):** `Executor`, `ExecutionResult`, `StepExecution`,
  `ExecutionContext`, the `ToolInvoker` abstraction + `EchoToolInvoker` stub, and the
  `ExecutionOutcome` / `ExecutionErrorCategory` enums. Runs only authorized steps in plan order
  through the registry-gated invoker, records an immutable audit trail — no planning, permission
  evaluation, engine access, or LLM.

All deterministic (ids + checksums) and serialization round-trip. **No routing / LLM** (later
milestones). Imports nothing from any engine.
"""

from __future__ import annotations

from app.agent.models import (
    AGENT_VERSION,
    Agent,
    AgentError,
    AgentPlan,
    AgentSession,
    AgentState,
    AgentTask,
    AuditEntry,
    ExecutionState,
    ExecutionStep,
    InvalidAgentTransitionError,
    InvalidPlanError,
    InvalidToolCallError,
    PermissionDecision,
    PermissionRequest,
    SchemaConsistencyError,
    TaskStatus,
    ToolCall,
    ToolResult,
    UnsupportedVersionError,
)
from app.agent.tools import (
    DEFAULT_TOOL_VERSION,
    TOOL_REGISTRY_VERSION,
    DuplicateToolError,
    InvalidCategoryError,
    InvalidToolDefinitionError,
    InvalidToolSchemaError,
    ToolAvailability,
    ToolCapability,
    ToolCategory,
    ToolDefinition,
    ToolError,
    ToolNotFoundError,
    ToolParameter,
    ToolRegistry,
    ToolSchema,
    UnsupportedToolRegistryVersionError,
    default_registry,
    default_tool_definitions,
)
from app.agent.planner import (
    DEFAULT_PLANNING_RULES,
    PLANNER_VERSION,
    PlannerError,
    PlannerErrorCategory,
    Planner,
    PlanningResult,
    PlanningRule,
    PlanningStatus,
    PlanStepSpec,
)
from app.agent.permissions import (
    PERMISSION_ENGINE_VERSION,
    AuthorizationResult,
    PermissionEngine,
    PermissionEngineError,
    PermissionErrorCategory,
    PermissionLevel,
    PermissionPolicy,
    PermissionRule,
    StepAuthorization,
    default_policy,
)
from app.agent.executor import (
    EXECUTOR_VERSION,
    EchoToolInvoker,
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionOutcome,
    ExecutionResult,
    Executor,
    StepExecution,
    ToolInvoker,
)

__all__ = [
    "AGENT_VERSION",
    # enums
    "AgentState",
    "ExecutionState",
    "TaskStatus",
    "PermissionDecision",
    # models
    "Agent",
    "AgentSession",
    "AgentTask",
    "AgentPlan",
    "ExecutionStep",
    "ToolCall",
    "ToolResult",
    "PermissionRequest",
    "AuditEntry",
    # errors
    "AgentError",
    "InvalidAgentTransitionError",
    "InvalidToolCallError",
    "InvalidPlanError",
    "SchemaConsistencyError",
    "UnsupportedVersionError",
    # --- M2: Tool Registry ---
    "TOOL_REGISTRY_VERSION",
    "DEFAULT_TOOL_VERSION",
    # tool enums
    "ToolCategory",
    "ToolCapability",
    "ToolAvailability",
    # tool models
    "ToolParameter",
    "ToolSchema",
    "ToolDefinition",
    "ToolRegistry",
    # default catalog
    "default_registry",
    "default_tool_definitions",
    # tool errors
    "ToolError",
    "InvalidToolDefinitionError",
    "InvalidToolSchemaError",
    "InvalidCategoryError",
    "DuplicateToolError",
    "ToolNotFoundError",
    "UnsupportedToolRegistryVersionError",
    # --- M3: Planner ---
    "PLANNER_VERSION",
    "Planner",
    "PlanningResult",
    "PlanningRule",
    "PlanStepSpec",
    "PlanningStatus",
    "PlannerErrorCategory",
    "PlannerError",
    "DEFAULT_PLANNING_RULES",
    # --- M4: Permission Engine ---
    "PERMISSION_ENGINE_VERSION",
    "PermissionEngine",
    "PermissionPolicy",
    "PermissionRule",
    "AuthorizationResult",
    "StepAuthorization",
    "PermissionLevel",
    "PermissionErrorCategory",
    "PermissionEngineError",
    "default_policy",
    # --- M5: Executor ---
    "EXECUTOR_VERSION",
    "Executor",
    "ExecutionResult",
    "StepExecution",
    "ExecutionContext",
    "ToolInvoker",
    "EchoToolInvoker",
    "ExecutionOutcome",
    "ExecutionErrorCategory",
    "ExecutionError",
]
