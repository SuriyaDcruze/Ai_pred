"""Conversation Engine for the Conversation Intelligence Engine (Sprint 6 · Milestone 5).

Orchestrates conversation **sessions, multi-turn interactions, context, and lifecycle** by
coordinating the completed modules from Milestones 1–4. It **does not** execute an LLM, retrieve
Decision Intelligence, build prompts, generate responses, or calculate confidence — it *coordinates*
only. Its message pipeline runs Intent Detection (M2), maintains conversation memory, drives a
deterministic lifecycle state machine, and produces an **orchestration result** describing what the
next step (retrieval / clarification) should be — but it stops **before** retrieval execution.

In-memory only (no database persistence). Deterministic: identical sessions + messages produce
identical orchestration results (a SHA-256 checksum proves it). Imports no engine, no LLM, and does
not execute the retrieval/prompt modules.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.conversation.intent import (
    INTENT_REGISTRY,
    INTENT_VERSION,
    Intent,
    IntentClassification,
    IntentClassifier,
    IntentValidation,
)
from app.conversation.models import (
    CONVERSATION_VERSION,
    ConversationContext,
    ConversationSession,
    Message,
    Role,
)
from app.conversation.prompt import PROMPT_VERSION
from app.conversation.retrieval import RETRIEVAL_VERSION

#: The Conversation Engine method/schema version.
ENGINE_VERSION: str = "eng-1"
#: How many recent messages the multi-turn context window keeps (deterministic pruning).
DEFAULT_CONTEXT_WINDOW: int = 10


# --------------------------------------------------------------------------- enums
class LifecycleState(str, Enum):
    """A conversation's deterministic lifecycle state."""

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class FollowUpStatus(str, Enum):
    """The pending conversational state after a turn (never infers missing information)."""

    CONTINUATION = "CONTINUATION"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    MISSING_ENTITY = "MISSING_ENTITY"
    COMPLETED = "COMPLETED"


class NextStep(str, Enum):
    """What the engine hands off to next (it does not execute these here)."""

    RETRIEVAL = "RETRIEVAL"
    CLARIFY = "CLARIFY"
    NONE = "NONE"


#: Allowed lifecycle transitions (terminal states have none). Self-loops are always allowed.
_ALLOWED: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.CREATED: {LifecycleState.ACTIVE, LifecycleState.WAITING_FOR_INPUT,
                             LifecycleState.COMPLETED, LifecycleState.ERROR, LifecycleState.EXPIRED},
    LifecycleState.ACTIVE: {LifecycleState.WAITING_FOR_INPUT, LifecycleState.COMPLETED,
                            LifecycleState.ERROR, LifecycleState.EXPIRED},
    LifecycleState.WAITING_FOR_INPUT: {LifecycleState.ACTIVE, LifecycleState.COMPLETED,
                                       LifecycleState.ERROR, LifecycleState.EXPIRED},
    LifecycleState.COMPLETED: set(),
    LifecycleState.EXPIRED: set(),
    LifecycleState.ERROR: set(),
}
_TERMINAL = {LifecycleState.COMPLETED, LifecycleState.EXPIRED, LifecycleState.ERROR}


# --------------------------------------------------------------------------- errors
class EngineError(Exception):
    """Base class for conversation-engine errors."""


class SessionNotFoundError(EngineError):
    """No session with the given id."""


class InvalidTransitionError(EngineError):
    """An illegal lifecycle transition (e.g. a message on a terminal session)."""


class DuplicateMessageError(EngineError):
    """The same user message was submitted consecutively."""


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _versions() -> dict[str, str]:
    return {"engine_version": ENGINE_VERSION, "conversation_version": CONVERSATION_VERSION,
            "intent_version": INTENT_VERSION, "retrieval_version": RETRIEVAL_VERSION,
            "prompt_version": PROMPT_VERSION}


# --------------------------------------------------------------------------- orchestration result
@dataclass(frozen=True)
class OrchestrationResult:
    """The deterministic outcome of one conversation turn — session snapshot + state + intent +
    follow-up + next step. No response, no retrieval, no prompt (those are later steps)."""

    session_id: str
    state: LifecycleState
    intent: IntentClassification | None
    follow_up: FollowUpStatus
    next_step: NextStep
    session: ConversationSession
    versions: dict[str, str]
    checksum: str
    version: str = ENGINE_VERSION

    def stable_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "state": self.state.value,
            "intent": self.intent.stable_dict() if self.intent else None,
            "follow_up": self.follow_up.value, "next_step": self.next_step.value,
            "session": self.session.stable_dict(), "versions": self.versions, "version": self.version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum}

    def serialize(self) -> str:
        return json.dumps(self.stable_dict(), sort_keys=True, separators=(",", ":"))


@dataclass
class _SessionRecord:
    """Internal, in-memory session state (the latest immutable snapshot + orchestration state)."""

    session: ConversationSession
    state: LifecycleState
    follow_up: FollowUpStatus
    entities: dict[str, str] = field(default_factory=dict)
    turns: int = 0
    last_user_content: str | None = None
    pending_intent: Intent | None = None    # an intent awaiting a clarifying subject


# --------------------------------------------------------------------------- the engine
class ConversationEngine:
    """Deterministically orchestrates conversation sessions (in-memory; coordination only)."""

    def __init__(self, *, classifier: IntentClassifier | None = None,
                 context_window: int = DEFAULT_CONTEXT_WINDOW) -> None:
        self._classifier = classifier or IntentClassifier()
        self._window = context_window
        self._sessions: dict[str, _SessionRecord] = {}

    # ---- session management ---------------------------------------------
    def create_session(self, *, session_id: str | None = None,
                       title: str | None = None) -> ConversationSession:
        sid = session_id or uuid.uuid4().hex
        session = ConversationSession.create(session_id=sid, title=title)
        self._sessions[sid] = _SessionRecord(session=session, state=LifecycleState.CREATED,
                                             follow_up=FollowUpStatus.CONTINUATION)
        return session

    def get_session(self, session_id: str) -> ConversationSession:
        return self._record(session_id).session

    def state_of(self, session_id: str) -> LifecycleState:
        return self._record(session_id).state

    def history(self, session_id: str, *, window: int | None = None) -> tuple[Message, ...]:
        """The multi-turn context window — the most recent messages (deterministic pruning)."""
        messages = self._record(session_id).session.messages
        limit = window or self._window
        return messages[-limit:]

    def _record(self, session_id: str) -> _SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise SessionNotFoundError(f"unknown session {session_id!r}")
        return record

    # ---- message pipeline ------------------------------------------------
    def handle_message(self, session_id: str, text: str) -> OrchestrationResult:
        """Run the deterministic message pipeline for a user turn.

        Raises:
            SessionNotFoundError / InvalidTransitionError / DuplicateMessageError.
        """
        record = self._record(session_id)
        if record.state in _TERMINAL:                       # can't converse on a closed session
            raise InvalidTransitionError(f"session is {record.state.value}")
        if not isinstance(text, str) or not text.strip():
            raise InvalidTransitionError("message text must be a non-empty string")
        if record.last_user_content is not None and text == record.last_user_content:
            raise DuplicateMessageError("duplicate consecutive user message")

        # Intent Detection (M2), with accumulated entities as multi-turn memory (never infers).
        classification = self._classifier.classify(text, context=record.entities)
        record.entities = {**record.entities, **classification.entities}

        # Follow-up resume: a reply that supplies the awaited subject continues the pending intent.
        if (record.state is LifecycleState.WAITING_FOR_INPUT and record.pending_intent is not None
                and classification.intent in (Intent.UNKNOWN, record.pending_intent)
                and self._subject_ok(record.pending_intent, record.entities)):
            classification = IntentClassification(
                intent=record.pending_intent, confidence=classification.confidence,
                matched_rules=classification.matched_rules, entities=dict(record.entities),
                validation=IntentValidation(True))

        follow_up, next_step, target_state = self._route(classification)
        record.pending_intent = (classification.intent
                                 if follow_up is FollowUpStatus.MISSING_ENTITY else None)
        self._transition(record, target_state)

        session = record.session.add_message(
            Role.USER, text, metadata={"intent": classification.intent.value,
                                       "follow_up": follow_up.value})
        session = session.with_context(ConversationContext(
            subject_kind=("prediction" if record.entities.get("prediction_id")
                          else "symbol" if record.entities.get("symbol") else None),
            subject_id=record.entities.get("prediction_id") or record.entities.get("symbol"),
            data={"entities": dict(record.entities)},
            versions={"engine_version": ENGINE_VERSION}))
        record.session = session
        record.follow_up = follow_up
        record.turns += 1
        record.last_user_content = text
        return self._result(record, classification, next_step)

    def close_session(self, session_id: str) -> OrchestrationResult:
        record = self._record(session_id)
        self._transition(record, LifecycleState.COMPLETED)
        record.follow_up = FollowUpStatus.COMPLETED
        return self._result(record, None, NextStep.NONE)

    def expire_session(self, session_id: str) -> OrchestrationResult:
        record = self._record(session_id)
        self._transition(record, LifecycleState.EXPIRED)
        record.follow_up = FollowUpStatus.COMPLETED
        return self._result(record, None, NextStep.NONE)

    # ---- internals -------------------------------------------------------
    @staticmethod
    def _subject_ok(intent: Intent, entities: dict[str, str]) -> bool:
        spec = INTENT_REGISTRY.get(intent)
        if spec is None or "subject" not in spec.required_entities:
            return True
        return bool(entities.get("prediction_id") or entities.get("symbol"))

    @staticmethod
    def _route(classification: IntentClassification) -> tuple[FollowUpStatus, NextStep, LifecycleState]:
        if classification.intent is Intent.UNKNOWN:
            return FollowUpStatus.CLARIFICATION_REQUIRED, NextStep.CLARIFY, LifecycleState.WAITING_FOR_INPUT
        if not classification.validation.valid:
            return FollowUpStatus.MISSING_ENTITY, NextStep.CLARIFY, LifecycleState.WAITING_FOR_INPUT
        return FollowUpStatus.CONTINUATION, NextStep.RETRIEVAL, LifecycleState.ACTIVE

    @staticmethod
    def _transition(record: _SessionRecord, target: LifecycleState) -> None:
        if target is not record.state and target not in _ALLOWED[record.state]:
            raise InvalidTransitionError(f"cannot transition {record.state.value} -> {target.value}")
        record.state = target

    @staticmethod
    def _result(record: _SessionRecord, classification: IntentClassification | None,
                next_step: NextStep) -> OrchestrationResult:
        versions = _versions()
        payload = {
            "session_id": record.session.session_id, "state": record.state.value,
            "intent": classification.stable_dict() if classification else None,
            "follow_up": record.follow_up.value, "next_step": next_step.value,
            "session": record.session.stable_dict(), "versions": versions,
        }
        return OrchestrationResult(
            session_id=record.session.session_id, state=record.state, intent=classification,
            follow_up=record.follow_up, next_step=next_step, session=record.session,
            versions=versions, checksum=_sha256(payload),
        )
