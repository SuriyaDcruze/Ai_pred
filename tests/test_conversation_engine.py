"""Tests for the Conversation Engine (Sprint 6 · Milestone 5).

Cover session management, deterministic lifecycle transitions, the message pipeline, multi-turn
context + memory (follow-ups reuse the subject), follow-up routing, context pruning/window,
validation (duplicate / unknown session / terminal transitions), serialization, deterministic
output, versioning, and no-engine/LLM/retrieval-execution imports. Orchestration only.
"""

from __future__ import annotations

import pytest

from app.conversation.engine import (
    ENGINE_VERSION,
    ConversationEngine,
    DuplicateMessageError,
    FollowUpStatus,
    InvalidTransitionError,
    LifecycleState,
    NextStep,
    SessionNotFoundError,
)
from app.conversation.intent import Intent


@pytest.fixture()
def engine():
    return ConversationEngine()


# --------------------------------------------------------------- session management
def test_create_and_lookup_session(engine):
    s = engine.create_session(session_id="s1", title="t")
    assert s.session_id == "s1" and engine.state_of("s1") is LifecycleState.CREATED
    assert engine.get_session("s1").message_count == 0


def test_unknown_session_raises(engine):
    with pytest.raises(SessionNotFoundError):
        engine.get_session("nope")
    with pytest.raises(SessionNotFoundError):
        engine.handle_message("nope", "hi")


# --------------------------------------------------------------- message pipeline / routing
def test_full_request_is_continuation(engine):
    engine.create_session(session_id="s1")
    r = engine.handle_message("s1", "explain this prediction ab12cd34ef56ab78")
    assert r.intent.intent is Intent.EXPLAIN_PREDICTION
    assert r.follow_up is FollowUpStatus.CONTINUATION and r.next_step is NextStep.RETRIEVAL
    assert r.state is LifecycleState.ACTIVE and r.session.message_count == 1


def test_missing_subject_asks_for_it(engine):
    engine.create_session(session_id="s1")
    r = engine.handle_message("s1", "show me the evidence")
    assert r.follow_up is FollowUpStatus.MISSING_ENTITY and r.next_step is NextStep.CLARIFY
    assert r.state is LifecycleState.WAITING_FOR_INPUT


def test_unknown_request_requires_clarification(engine):
    engine.create_session(session_id="s1")
    r = engine.handle_message("s1", "the quick brown fox")
    assert r.intent.intent is Intent.UNKNOWN
    assert r.follow_up is FollowUpStatus.CLARIFICATION_REQUIRED and r.next_step is NextStep.CLARIFY


# --------------------------------------------------------------- multi-turn memory
def test_followup_reuses_subject_from_memory(engine):
    engine.create_session(session_id="s1")
    engine.handle_message("s1", "explain prediction ab12cd34ef56ab78")   # sets subject
    r2 = engine.handle_message("s1", "and why the confidence?")           # no subject in text
    assert r2.intent.intent is Intent.WHY_CONFIDENCE
    assert r2.intent.validation.valid                                     # subject recalled from memory
    assert r2.follow_up is FollowUpStatus.CONTINUATION and r2.state is LifecycleState.ACTIVE


def test_waiting_then_reply_returns_to_active(engine):
    engine.create_session(session_id="s1")
    engine.handle_message("s1", "show me the evidence")                   # WAITING (missing subject)
    assert engine.state_of("s1") is LifecycleState.WAITING_FOR_INPUT
    r = engine.handle_message("s1", "for BTCUSDT")                        # supplies subject
    assert engine.state_of("s1") is LifecycleState.ACTIVE and r.follow_up is FollowUpStatus.CONTINUATION


# --------------------------------------------------------------- lifecycle
def test_close_and_expire_are_terminal(engine):
    engine.create_session(session_id="s1")
    engine.handle_message("s1", "explain prediction ab12cd34ef56ab78")
    closed = engine.close_session("s1")
    assert closed.state is LifecycleState.COMPLETED
    with pytest.raises(InvalidTransitionError):
        engine.handle_message("s1", "another question about ab12cd34ef56ab78")

    engine.create_session(session_id="s2")
    engine.expire_session("s2")
    assert engine.state_of("s2") is LifecycleState.EXPIRED


def test_empty_message_rejected(engine):
    engine.create_session(session_id="s1")
    with pytest.raises(InvalidTransitionError):
        engine.handle_message("s1", "   ")


def test_duplicate_consecutive_message_rejected(engine):
    engine.create_session(session_id="s1")
    engine.handle_message("s1", "explain prediction ab12cd34ef56ab78")
    with pytest.raises(DuplicateMessageError):
        engine.handle_message("s1", "explain prediction ab12cd34ef56ab78")


# --------------------------------------------------------------- context window / pruning
def test_context_window_prunes_to_recent(engine):
    e = ConversationEngine(context_window=3)
    e.create_session(session_id="s1")
    for i in range(5):
        e.handle_message("s1", f"explain prediction ab12cd34ef56ab{i:02d}")
    window = e.history("s1")
    assert len(window) == 3 and [m.content[-2:] for m in window] == ["02", "03", "04"]
    assert e.get_session("s1").message_count == 5          # full history retained (only the view is pruned)


# --------------------------------------------------------------- determinism / serialization / versions
def test_deterministic_orchestration():
    def run():
        e = ConversationEngine()
        e.create_session(session_id="s1")
        return e.handle_message("s1", "explain prediction ab12cd34ef56ab78")
    a, b = run(), run()
    assert a.checksum == b.checksum and a.serialize() == b.serialize()


def test_serialization_and_versions(engine):
    engine.create_session(session_id="s1")
    r = engine.handle_message("s1", "explain prediction ab12cd34ef56ab78")
    d = r.to_dict()
    assert d["checksum"] and d["version"] == ENGINE_VERSION and d["state"] == "ACTIVE"
    assert set(r.versions) == {"engine_version", "conversation_version", "intent_version",
                               "retrieval_version", "prompt_version"}
    assert r.versions["engine_version"] == ENGINE_VERSION


# --------------------------------------------------------------- isolation
def test_engine_imports_no_execution_engine_or_llm():
    import ast

    import app.conversation.engine as eng
    with open(eng.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.decision_intelligence",
                 "app.conversation.sources", "openai")
    for name in imported:
        assert not name.startswith(forbidden), f"engine must not import {name}"
