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

**Current state (Milestones 1–3):** the **conversation domain model** (M1), the **Intent Detection
Engine** (M2), and the **Retrieval Orchestrator** (M3) — a deterministic, transport-independent
layer that retrieves Decision Intelligence data (decision / evidence / explanation / confidence /
history / similar / learning / health / version) into a conversation payload, **through the Decision
Intelligence Engine only** (no generation, no prompts, no LLM). Prompt building, the conversation
engine, the LLM adapter, and the REST API arrive in later milestones.
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
]
