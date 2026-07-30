"""Tests for the conversation domain model (Sprint 6 · Milestone 1).

Cover session + message creation, deterministic message ids, canonical roles/states/availability,
citations + context, monotonic-sequence validation, version metadata, checksum determinism,
serialization round-trips, immutability, and that the module imports no engine. Structure only — no
GPT, no retrieval, no API.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.conversation.models import (
    CONVERSATION_VERSION,
    Availability,
    Citation,
    ConversationContext,
    ConversationSession,
    ConversationStatus,
    InvalidMessageError,
    InvalidSessionError,
    Message,
    Role,
    SchemaConsistencyError,
    UnsupportedVersionError,
    _message_id,
)


# --------------------------------------------------------------- creation / defaults
def test_create_session_is_empty_active():
    s = ConversationSession.create(session_id="s1", title="Explain REL")
    assert s.status is ConversationStatus.ACTIVE and s.message_count == 0
    assert s.conversation_version == CONVERSATION_VERSION and s.last_message is None
    assert s.checksum and len(s.checksum) == 64


def test_add_messages_increments_sequence():
    s = ConversationSession.create(session_id="s1")
    s = s.add_message(Role.USER, "Explain this prediction")
    s = s.add_message(Role.ASSISTANT, "Historically ...")
    assert [m.sequence for m in s.messages] == [0, 1]
    assert [m.role for m in s.messages] == [Role.USER, Role.ASSISTANT]
    assert s.last_message.content == "Historically ..."


# --------------------------------------------------------------- deterministic ids
def test_message_id_deterministic_not_random():
    a = Message.create(session_id="s1", sequence=0, role=Role.USER, content="hi")
    b = Message.create(session_id="s1", sequence=0, role=Role.USER, content="hi")
    assert a.message_id == b.message_id == _message_id("s1", 0, "user", "hi")
    assert Message.create(session_id="s1", sequence=1, role=Role.USER, content="hi").message_id != a.message_id


# --------------------------------------------------------------- roles / states / availability
def test_roles_states_availability_enums():
    assert {r.value for r in Role} == {"user", "assistant", "system"}
    assert {s.value for s in ConversationStatus} == {"ACTIVE", "CLOSED"}
    assert {a.value for a in Availability} == {"AVAILABLE", "INSUFFICIENT_DATA", "NOT_AVAILABLE",
                                               "NOT_SUPPORTED"}


def test_availability_marker_on_message():
    s = ConversationSession.create(session_id="s1").add_message(
        Role.ASSISTANT, "No history yet.", availability=Availability.INSUFFICIENT_DATA)
    assert s.messages[0].availability is Availability.INSUFFICIENT_DATA


def test_close_session():
    s = ConversationSession.create(session_id="s1").close()
    assert s.status is ConversationStatus.CLOSED


# --------------------------------------------------------------- citations / context
def test_citations_carried_on_message():
    cite = Citation(kind="decision", ref_id="d1", source="decision_intelligence")
    s = ConversationSession.create(session_id="s1").add_message(
        Role.ASSISTANT, "See the decision.", citations=[cite])
    assert s.messages[0].citations == (cite,)


def test_citation_requires_fields():
    with pytest.raises(InvalidMessageError):
        Citation(kind="", ref_id="d1", source="x")


def test_context_attached():
    ctx = ConversationContext(subject_kind="prediction", subject_id="p1", data={"symbol": "REL.NS"})
    s = ConversationSession.create(session_id="s1").with_context(ctx)
    assert s.context.subject_id == "p1" and s.context.data["symbol"] == "REL.NS"


# --------------------------------------------------------------- validation
def test_bad_role_rejected():
    with pytest.raises(InvalidMessageError):
        Message(message_id="x", session_id="s1", sequence=0, role="user", content="hi")  # type: ignore[arg-type]


def test_foreign_message_rejected():
    other = Message.create(session_id="other", sequence=0, role=Role.USER, content="hi")
    with pytest.raises(InvalidSessionError):
        ConversationSession.create(session_id="s1").append(other)


def test_non_monotonic_sequence_rejected():
    s = ConversationSession.create(session_id="s1")
    skip = Message.create(session_id="s1", sequence=3, role=Role.USER, content="hi")
    with pytest.raises(SchemaConsistencyError):
        s.append(skip)


def test_unsupported_version_rejected():
    with pytest.raises(UnsupportedVersionError):
        ConversationSession.create(session_id="s1", version="cnv-999")


# --------------------------------------------------------------- checksum / immutability
def test_checksum_deterministic():
    def build():
        s = ConversationSession.create(session_id="s1", title="t")
        return s.add_message(Role.USER, "q").add_message(Role.ASSISTANT, "a")
    assert build().checksum == build().checksum
    other = ConversationSession.create(session_id="s1", title="t").add_message(Role.USER, "different")
    assert other.checksum != build().checksum


def test_session_and_message_are_frozen():
    s = ConversationSession.create(session_id="s1").add_message(Role.USER, "hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.status = ConversationStatus.CLOSED       # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.messages[0].content = "x"                # type: ignore[misc]


def test_append_returns_new_session():
    s0 = ConversationSession.create(session_id="s1")
    s1 = s0.add_message(Role.USER, "hi")
    assert s0.message_count == 0 and s1.message_count == 1   # original unchanged (functional update)


# --------------------------------------------------------------- serialization
def test_session_round_trip():
    ctx = ConversationContext(subject_kind="prediction", subject_id="p1")
    s = ConversationSession.create(session_id="s1", title="t", context=ctx).add_message(
        Role.USER, "explain").add_message(
        Role.ASSISTANT, "ok", availability=Availability.AVAILABLE,
        citations=[Citation(kind="decision", ref_id="d1", source="decision_intelligence")])
    got = ConversationSession.from_dict(s.to_dict())
    assert got == s and got.checksum == s.checksum and got.stable_dict() == s.stable_dict()


def test_message_round_trip():
    m = Message.create(session_id="s1", sequence=0, role=Role.ASSISTANT, content="hi",
                       availability=Availability.NOT_SUPPORTED, metadata={"k": "v"})
    assert Message.from_dict(m.to_dict()) == m


# --------------------------------------------------------------- isolation
def test_models_module_imports_no_engine():
    import ast

    import app.conversation.models as m
    with open(m.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.decision_intelligence",
                 "app.memory", "app.similarity", "app.learning", "app.forward_testing", "app.chat")
    for name in imported:
        assert not name.startswith(forbidden), f"M1 must not import {name}"
