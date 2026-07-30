"""Conversation domain model for the GPT / Conversation Intelligence Engine (Sprint 6 · Milestone 1).

The GPT / Conversation Intelligence Engine is a **read-only explanation layer** over the completed
Decision Intelligence Engine (Sprint 5). It lets a user ask, in natural language, to *explain* an
existing prediction, its evidence, historical behaviour, similarity results, learning observations,
or system status — the LLM **explains only**; it never predicts, trains, recalculates confidence,
generates signals, or modifies data. This module defines **only the conversation domain model**:
messages, sessions, conversation context, metadata, serialization, and versioning.

**Milestone 1 is structure only.** No GPT calls, no retrieval, no prompts, no REST API — and it
imports nothing from any engine (asserted). Ids are deterministic, sessions serialise round-trip,
and a SHA-256 checksum fingerprints a session's content (volatile fields excluded) so the same
content always yields the same checksum.

**Distinct from** the legacy chat assistant in `app/chat/` (`TradingAssistant` /`LLMAssistant`), a
pre-Decision-Intelligence rule-based + optional-LLM layer — a separate concern this engine does not
modify.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

#: The conversation method/schema version. A shape/method change is a new version, never an edit.
CONVERSATION_VERSION: str = "cnv-1"


# --------------------------------------------------------------------------- enums
class Role(str, Enum):
    """Who authored a message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ConversationStatus(str, Enum):
    """A session's lifecycle state (lifecycle handling itself is a later milestone)."""

    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class Availability(str, Enum):
    """The honesty vocabulary an answer/message uses when information is absent — never fabricated.
    (Populated by later retrieval/generation milestones; established here as the domain contract.)"""

    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_SUPPORTED = "NOT_SUPPORTED"


# --------------------------------------------------------------------------- errors
class ConversationError(Exception):
    """Base class for every conversation-domain error."""


class InvalidMessageError(ConversationError):
    """A message is malformed (bad role / content / sequence)."""


class InvalidSessionError(ConversationError):
    """A session is malformed, or a message does not belong to it / breaks sequence."""


class UnsupportedVersionError(ConversationError):
    """A conversation version this build does not support."""


class SchemaConsistencyError(ConversationError):
    """A session's shape is inconsistent (bad id, non-monotonic messages)."""


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


# --------------------------------------------------------------------------- citation
@dataclass(frozen=True)
class Citation:
    """A reference from a message back to an existing system output — the traceability primitive for
    conversational answers (populated by later retrieval milestones; never invented)."""

    kind: str                       # "decision" | "evidence" | "recommendation" | "neighbour" | ...
    ref_id: str                     # the source id (decision_id / prediction_id / recommendation_id …)
    source: str                     # the owning subsystem/API (e.g. "decision_intelligence")
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.kind or not self.ref_id or not self.source:
            raise InvalidMessageError("Citation requires kind, ref_id, and source")

    def stable_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref_id": self.ref_id, "source": self.source, "note": self.note}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Citation":
        return cls(kind=_get(data, "kind"), ref_id=_get(data, "ref_id"), source=_get(data, "source"),
                   note=_get(data, "note"))


# --------------------------------------------------------------------------- message
def _message_id(session_id: str, sequence: int, role: str, content: str) -> str:
    """A deterministic message id — a function of its identity, so identical content in the same
    slot always keys the same (never random)."""
    raw = f"{session_id}|{sequence}|{role}|{content}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Message:
    """One turn in a conversation — authored by the user, the assistant, or the system.

    Immutable; its id is deterministic. `availability` + `citations` carry the honesty/traceability
    contract for assistant answers (filled by later milestones; a user message is simply
    `AVAILABLE` with no citations)."""

    message_id: str
    session_id: str
    sequence: int
    role: Role
    content: str
    availability: Availability = Availability.AVAILABLE
    citations: tuple[Citation, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            raise InvalidMessageError(f"invalid role {self.role!r}")
        if not isinstance(self.availability, Availability):
            raise InvalidMessageError(f"invalid availability {self.availability!r}")
        if not isinstance(self.content, str):
            raise InvalidMessageError("message content must be a string")
        if self.sequence < 0:
            raise InvalidMessageError("message sequence must be >= 0")

    @classmethod
    def create(
        cls, *, session_id: str, sequence: int, role: Role, content: str,
        availability: Availability = Availability.AVAILABLE,
        citations: "tuple[Citation, ...] | list[Citation]" = (),
        metadata: dict[str, Any] | None = None,
    ) -> "Message":
        return cls(
            message_id=_message_id(session_id, sequence, role.value, content), session_id=session_id,
            sequence=sequence, role=role, content=content, availability=availability,
            citations=tuple(citations), metadata=metadata or {},
        )

    def stable_dict(self) -> dict[str, Any]:
        """Deterministic content (excludes `created_at`) — for the session checksum."""
        return {
            "message_id": self.message_id, "session_id": self.session_id, "sequence": self.sequence,
            "role": self.role.value, "content": self.content, "availability": self.availability.value,
            "citations": [c.stable_dict() for c in self.citations], "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Message":
        return cls(
            message_id=_get(data, "message_id"), session_id=_get(data, "session_id"),
            sequence=int(_get(data, "sequence", 0)), role=Role(_get(data, "role")),
            content=_get(data, "content", ""),
            availability=Availability(_get(data, "availability", Availability.AVAILABLE.value)),
            citations=tuple(Citation.from_dict(c) for c in (_get(data, "citations") or [])),
            metadata=dict(_get(data, "metadata") or {}), created_at=_get(data, "created_at"),
        )


# --------------------------------------------------------------------------- context
@dataclass(frozen=True)
class ConversationContext:
    """What a conversation is *about* — the subject it references and any accumulated read-only
    context. Populated by later retrieval milestones; here it is the domain container only."""

    subject_kind: str | None = None      # "prediction" | "symbol" | "decision" | None
    subject_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    versions: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "ConversationContext":
        return cls()

    def stable_dict(self) -> dict[str, Any]:
        return {"subject_kind": self.subject_kind, "subject_id": self.subject_id,
                "data": self.data, "versions": self.versions}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConversationContext":
        return cls(subject_kind=_get(data, "subject_kind"), subject_id=_get(data, "subject_id"),
                   data=dict(_get(data, "data") or {}), versions=dict(_get(data, "versions") or {}))


# --------------------------------------------------------------------------- session
def _session_checksum(session_id: str, version: str, status: ConversationStatus, title: str | None,
                      context: ConversationContext, messages: "tuple[Message, ...]") -> str:
    payload = {
        "session_id": session_id, "conversation_version": version, "status": status.value,
        "title": title, "context": context.stable_dict(),
        "messages": [m.stable_dict() for m in messages],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ConversationSession:
    """A multi-turn conversation — an ordered, immutable list of messages + its context.

    Functional-update style: :meth:`append` / :meth:`add_message` return a **new** session (the
    engine that manages lifecycle is a later milestone). A SHA-256 `checksum` fingerprints the
    session's content (volatile `created_at` excluded), so identical content ⇒ identical checksum."""

    session_id: str
    conversation_version: str
    status: ConversationStatus
    title: str | None
    messages: tuple[Message, ...]
    context: ConversationContext
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if self.conversation_version != CONVERSATION_VERSION:
            raise UnsupportedVersionError(f"unsupported version {self.conversation_version!r}")
        if not self.session_id:
            raise SchemaConsistencyError("session_id is required")
        if not isinstance(self.status, ConversationStatus):
            raise InvalidSessionError(f"invalid status {self.status!r}")
        for index, message in enumerate(self.messages):        # monotonic, owned messages
            if message.session_id != self.session_id:
                raise InvalidSessionError(f"message {message.message_id} belongs to another session")
            if message.sequence != index:
                raise SchemaConsistencyError(f"message sequence {message.sequence} != slot {index}")

    @classmethod
    def create(
        cls, *, session_id: str | None = None, title: str | None = None,
        status: ConversationStatus = ConversationStatus.ACTIVE,
        context: ConversationContext | None = None, metadata: dict[str, Any] | None = None,
        version: str = CONVERSATION_VERSION,
    ) -> "ConversationSession":
        if version != CONVERSATION_VERSION:
            raise UnsupportedVersionError(f"unsupported version {version!r}")
        sid = session_id or uuid.uuid4().hex
        ctx = context or ConversationContext.empty()
        return cls(
            session_id=sid, conversation_version=version, status=status, title=title, messages=(),
            context=ctx, checksum=_session_checksum(sid, version, status, title, ctx, ()),
            metadata=metadata or {},
        )

    def append(self, message: Message) -> "ConversationSession":
        """Return a new session with `message` appended (validates ownership + sequence)."""
        if message.session_id != self.session_id:
            raise InvalidSessionError("message does not belong to this session")
        if message.sequence != len(self.messages):
            raise SchemaConsistencyError(
                f"message sequence {message.sequence} != next slot {len(self.messages)}"
            )
        messages = self.messages + (message,)
        return replace(self, messages=messages,
                       checksum=_session_checksum(self.session_id, self.conversation_version,
                                                  self.status, self.title, self.context, messages))

    def add_message(
        self, role: Role, content: str, *, availability: Availability = Availability.AVAILABLE,
        citations: "tuple[Citation, ...] | list[Citation]" = (), metadata: dict[str, Any] | None = None,
    ) -> "ConversationSession":
        """Build a message for the next slot (deterministic id) and append it (returns a new session)."""
        message = Message.create(
            session_id=self.session_id, sequence=len(self.messages), role=role, content=content,
            availability=availability, citations=citations, metadata=metadata,
        )
        return self.append(message)

    def with_context(self, context: ConversationContext) -> "ConversationSession":
        return replace(self, context=context,
                       checksum=_session_checksum(self.session_id, self.conversation_version,
                                                  self.status, self.title, context, self.messages))

    def close(self) -> "ConversationSession":
        return replace(self, status=ConversationStatus.CLOSED,
                       checksum=_session_checksum(self.session_id, self.conversation_version,
                                                  ConversationStatus.CLOSED, self.title, self.context,
                                                  self.messages))

    # ---- accessors -------------------------------------------------------
    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def last_message(self) -> Message | None:
        return self.messages[-1] if self.messages else None

    def stable_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "conversation_version": self.conversation_version,
            "status": self.status.value, "title": self.title, "context": self.context.stable_dict(),
            "messages": [m.stable_dict() for m in self.messages], "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum,
                "messages": [m.to_dict() for m in self.messages], "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConversationSession":
        return cls(
            session_id=_get(data, "session_id"),
            conversation_version=_get(data, "conversation_version", CONVERSATION_VERSION),
            status=ConversationStatus(_get(data, "status", ConversationStatus.ACTIVE.value)),
            title=_get(data, "title"),
            messages=tuple(Message.from_dict(m) for m in (_get(data, "messages") or [])),
            context=ConversationContext.from_dict(_get(data, "context") or {}),
            checksum=_get(data, "checksum", ""), metadata=dict(_get(data, "metadata") or {}),
            created_at=_get(data, "created_at"),
        )
