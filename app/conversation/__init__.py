"""GPT / Conversation Intelligence Engine — read-only explanation over Decision Intelligence (Sprint 6).

The Conversation Intelligence Engine lets a user ask, in natural language, to **explain** existing
AEGIS outputs — a prediction, its evidence, historical behaviour, similarity results, learning
observations, or system status. The LLM **explains only**: it never predicts, trains, recalculates
confidence, generates market signals, or modifies data, and it consumes **only** the completed
Decision Intelligence Engine (Sprint 5) — never the models directly. Where information does not
exist it says so (`INSUFFICIENT_DATA` / `NOT_AVAILABLE` / `NOT_SUPPORTED`) rather than hallucinating.

**Distinct from** the legacy chat assistant in `app/chat/` (`TradingAssistant` / `LLMAssistant`), a
pre-Decision-Intelligence rule-based + optional-LLM layer — a separate concern this engine does not
modify.

**Current state (Milestones 1–5):** the **conversation domain model** (M1), the **Intent Detection
Engine** (M2), the **Retrieval Orchestrator** (M3), the **Prompt Builder** (M4), and the
**Conversation Engine** (M5) — a deterministic, in-memory orchestrator of sessions, multi-turn
context, lifecycle, and follow-ups that coordinates M1–M4 (runs intent detection, drives the
lifecycle state machine, produces an orchestration result) **without** executing retrieval, prompts,
or an LLM. The LLM adapter and the REST API arrive in later milestones.
"""

from __future__ import annotations

from app.conversation.models import (
    CONVERSATION_VERSION,
    Availability,
    Citation,
    ConversationContext,
    ConversationError,
    ConversationSession,
    ConversationStatus,
    InvalidMessageError,
    InvalidSessionError,
    Message,
    Role,
    SchemaConsistencyError,
    UnsupportedVersionError,
)
from app.conversation.intent import (
    INTENT_REGISTRY,
    INTENT_VERSION,
    IntentClassification,
    IntentClassifier,
    IntentError,
    IntentSpec,
    IntentValidation,
    InvalidIntentInputError,
    Intent,
    UnknownIntentSpecError,
    available_intents,
    extract_entities,
    spec_for,
)
from app.conversation.retrieval import (
    RETRIEVAL_ROUTING,
    RETRIEVAL_VERSION,
    DecisionIntelligenceSource,
    InvalidRetrievalRequestError,
    RetrievalAvailability,
    RetrievalComponent,
    RetrievalError,
    RetrievalOrchestrator,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTarget,
)
from app.conversation.sources import InProcessSource
from app.conversation.prompt import (
    DEFAULT_TOKEN_BUDGET,
    INSTRUCTION_TEMPLATES,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    MissingCitationError,
    Prompt,
    PromptBlock,
    PromptBuilder,
    PromptError,
    PromptSection,
    PromptValidationError,
    TemplateError,
)
from app.conversation.engine import (
    DEFAULT_CONTEXT_WINDOW,
    ENGINE_VERSION,
    ConversationEngine,
    DuplicateMessageError,
    EngineError,
    FollowUpStatus,
    InvalidTransitionError,
    LifecycleState,
    NextStep,
    OrchestrationResult,
    SessionNotFoundError,
)

__all__ = [
    "CONVERSATION_VERSION",
    "Role",
    "ConversationStatus",
    "Availability",
    "Citation",
    "Message",
    "ConversationContext",
    "ConversationSession",
    # intent detection (M2)
    "Intent",
    "IntentSpec",
    "IntentClassifier",
    "IntentClassification",
    "IntentValidation",
    "INTENT_REGISTRY",
    "INTENT_VERSION",
    "available_intents",
    "spec_for",
    "extract_entities",
    # retrieval orchestrator (M3)
    "RetrievalOrchestrator",
    "DecisionIntelligenceSource",
    "InProcessSource",
    "RetrievalTarget",
    "RetrievalAvailability",
    "RetrievalRequest",
    "RetrievalComponent",
    "RetrievalResult",
    "RETRIEVAL_ROUTING",
    "RETRIEVAL_VERSION",
    # prompt builder (M4)
    "PromptBuilder",
    "Prompt",
    "PromptBlock",
    "PromptSection",
    "SYSTEM_PROMPT",
    "INSTRUCTION_TEMPLATES",
    "PROMPT_VERSION",
    "DEFAULT_TOKEN_BUDGET",
    # conversation engine (M5)
    "ConversationEngine",
    "OrchestrationResult",
    "LifecycleState",
    "FollowUpStatus",
    "NextStep",
    "ENGINE_VERSION",
    "DEFAULT_CONTEXT_WINDOW",
    # errors
    "ConversationError",
    "InvalidMessageError",
    "InvalidSessionError",
    "UnsupportedVersionError",
    "SchemaConsistencyError",
    "IntentError",
    "InvalidIntentInputError",
    "UnknownIntentSpecError",
    "RetrievalError",
    "InvalidRetrievalRequestError",
    "PromptError",
    "MissingCitationError",
    "PromptValidationError",
    "TemplateError",
    "EngineError",
    "SessionNotFoundError",
    "InvalidTransitionError",
    "DuplicateMessageError",
]
