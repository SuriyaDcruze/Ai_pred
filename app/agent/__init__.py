"""Agent Engine — deterministic planning + permissioned tool execution over AEGIS (Sprint 7).

The Agent Engine (later milestones) will plan and — under **explicit permission** — execute tool
calls against the existing **read-only** AEGIS engines, with a full immutable audit trail. It never
predicts or advises; it orchestrates existing deterministic capabilities.

**Current state (Milestone 1):** the **Agent domain model** only — `Agent`, `AgentSession` (+ its
validated lifecycle state machine), `AgentTask`, `AgentPlan`, `ExecutionStep`, `ToolCall`,
`ToolResult`, `PermissionRequest`, and the immutable `AuditEntry`. Deterministic ids, checksums, and
serialization. **No execution, tools, routing, LLM, permissions logic, or planning** (those are
later milestones). Imports nothing from any engine.
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
]
