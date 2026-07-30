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

All deterministic (ids + checksums) and serialization round-trip. **No execution, routing, LLM,
permissions logic, or planning** (those are later milestones). Imports nothing from any engine.
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
]
