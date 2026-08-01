"""Workflow Engine — deterministic orchestration of many Agent Engine executions (Sprint 8).

The Workflow Engine (later milestones) will orchestrate **multiple Agent Engine executions** into a
durable, resumable, auditable process — sequential / conditional / parallel branches, retries,
rollback hooks, waiting + approval checkpoints, scheduling, timeout, cancellation, and resume-after-
interruption. It sits **above** the Agent Engine and reaches the AEGIS engines **only through** it; it
never predicts, trains, bypasses permissions, replaces the Agent Engine, or executes business logic.

**Current state (Milestone 1):** the **Workflow domain model** only — `Workflow`, `WorkflowSession`
(+ its validated lifecycle state machine), `WorkflowDefinition`, `WorkflowStep`, `WorkflowTransition`,
`WorkflowExecution`, the immutable `WorkflowEvent`, `WorkflowCheckpoint`, and `WorkflowResult`.
Deterministic ids, checksums, and serialization. **No runtime, transitions, scheduling, retries,
checkpoint storage, agent invocation, REST, or persistence** (those are later milestones). Imports
nothing from any engine — not the Prediction engine, not the Outcome engine, and not the Agent Engine.
"""

from __future__ import annotations

from app.workflow.models import (
    WORKFLOW_VERSION,
    InvalidWorkflowDefinitionError,
    InvalidWorkflowEventError,
    InvalidWorkflowTransitionError,
    SchemaConsistencyError,
    StepKind,
    StepState,
    TransitionKind,
    UnsupportedVersionError,
    Workflow,
    WorkflowCheckpoint,
    WorkflowDefinition,
    WorkflowError,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowExecution,
    WorkflowOutcome,
    WorkflowResult,
    WorkflowSession,
    WorkflowState,
    WorkflowStep,
    WorkflowTransition,
)

__all__ = [
    "WORKFLOW_VERSION",
    # enums
    "WorkflowState",
    "StepState",
    "StepKind",
    "TransitionKind",
    "WorkflowEventType",
    "WorkflowOutcome",
    # models
    "Workflow",
    "WorkflowSession",
    "WorkflowDefinition",
    "WorkflowStep",
    "WorkflowTransition",
    "WorkflowExecution",
    "WorkflowEvent",
    "WorkflowCheckpoint",
    "WorkflowResult",
    # errors
    "WorkflowError",
    "InvalidWorkflowTransitionError",
    "InvalidWorkflowDefinitionError",
    "InvalidWorkflowEventError",
    "SchemaConsistencyError",
    "UnsupportedVersionError",
]
