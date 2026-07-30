"""Conversation REST API (`/chat/*`, Sprint 6 · Milestone 7).

A **thin transport** over the completed Conversation Intelligence Engine (M1–M6). It owns transport
only — it performs **no** conversation logic: it validates a request, then **orchestrates the
completed pipeline** (Conversation Engine → Retrieval Orchestrator → Prompt Builder → LLM Adapter)
and serialises a normalised response. It classifies no intents, retrieves no Decision Intelligence
directly, builds no prompts in the routes, and calls no provider directly — every step is delegated
to the corresponding conversation module. It imports neither the Prediction nor the Outcome engine.

Route ownership: a **legacy exact `POST /chat`** (the pre-Decision-Intelligence TradingAssistant)
already exists. To avoid a collision — and because superseding the legacy chat is out of scope for
Sprint 6 — this router owns the `/chat/*` **sub-namespace** and exposes its message endpoint as
`POST /chat/message` (the legacy exact `POST /chat` is left untouched). Read-only; writes nothing.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.conversation.engine import (
    ENGINE_VERSION,
    ConversationEngine,
    DuplicateMessageError,
    FollowUpStatus,
    InvalidTransitionError,
    NextStep,
    SessionNotFoundError,
)
from app.conversation.intent import INTENT_VERSION
from app.conversation.llm_adapter import (
    LLM_ADAPTER_VERSION,
    LLMErrorCategory,
    LLMRequest,
    create_adapter,
)
from app.conversation.models import CONVERSATION_VERSION
from app.conversation.prompt import PROMPT_VERSION, PromptBuilder
from app.conversation.retrieval import RETRIEVAL_VERSION, RetrievalOrchestrator
from app.conversation.sources import InProcessSource
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["conversation"])

API_VERSION: str = "1"
_MODEL = "aegis-explainer"
#: LLM error category → HTTP status.
_HTTP_FROM_LLM = {
    LLMErrorCategory.RATE_LIMITED: 429, LLMErrorCategory.PROVIDER_UNAVAILABLE: 503,
    LLMErrorCategory.TIMEOUT: 503, LLMErrorCategory.INVALID_REQUEST: 400,
    LLMErrorCategory.AUTHENTICATION_ERROR: 500, LLMErrorCategory.INTERNAL_ERROR: 500,
}
_CLARIFY = {
    FollowUpStatus.MISSING_ENTITY: "To answer that I need a prediction id or a symbol — which one?",
    FollowUpStatus.CLARIFICATION_REQUIRED: ("I can explain a prediction, its evidence, confidence, "
                                            "historical behaviour, similar cases, or learning — could "
                                            "you rephrase?"),
}


# --------------------------------------------------------------------------- schemas
class ChatMessageRequest(BaseModel):
    message: str
    session_id: str | None = None
    metadata: dict[str, Any] = {}
    client: dict[str, Any] | None = None


class SessionCreateRequest(BaseModel):
    title: str | None = None


class ChatMessageResponse(BaseModel):
    response: str
    session_id: str
    conversation_status: str
    availability_status: str
    citations: list[dict[str, Any]]
    intent: str | None
    next_step: str
    versions: dict[str, str]


class SessionResponse(BaseModel):
    session_id: str
    status: str
    message_count: int
    messages: list[dict[str, Any]]
    versions: dict[str, str]


class HealthResponse(BaseModel):
    status: str
    components: dict[str, Any]
    versions: dict[str, str]


class VersionResponse(BaseModel):
    api_version: str
    versions: dict[str, str]


# --------------------------------------------------------------------------- helpers
def _versions() -> dict[str, str]:
    return {"conversation_version": CONVERSATION_VERSION, "intent_version": INTENT_VERSION,
            "retrieval_version": RETRIEVAL_VERSION, "prompt_version": PROMPT_VERSION,
            "engine_version": ENGINE_VERSION, "llm_adapter_version": LLM_ADAPTER_VERSION,
            "api_version": API_VERSION}


@contextmanager
def _observe(endpoint: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
        logger.info("chat api %s ok in %.1fms", endpoint, (time.perf_counter() - start) * 1000)
    except HTTPException as exc:
        logger.info("chat api %s -> %d in %.1fms", endpoint, exc.status_code,
                    (time.perf_counter() - start) * 1000)
        raise


def _pipeline(request: Request) -> dict[str, Any]:
    """Get-or-build the (cached) conversation pipeline from app.state — the engine (with sessions)
    persists across requests. Coordination only; the modules do the work."""
    state = request.app.state
    pipe = getattr(state, "conversation_pipeline", None)
    if pipe is None:
        forward_store = getattr(state, "forward_store", None)
        retrieval = getattr(state, "retrieval", None)
        if forward_store is None or retrieval is None:
            raise HTTPException(status_code=503, detail="conversation dependencies unavailable")
        source = InProcessSource(forward_store, retrieval)
        pipe = {
            "engine": ConversationEngine(),
            "orchestrator": RetrievalOrchestrator(source),
            "prompt_builder": PromptBuilder(),
            "llm": getattr(state, "conversation_llm", None) or create_adapter("echo"),
            "source": source,
        }
        state.conversation_pipeline = pipe
    return pipe


# --------------------------------------------------------------------------- endpoints
@router.post("/message", response_model=ChatMessageResponse)
async def chat_message(body: ChatMessageRequest, request: Request) -> ChatMessageResponse:
    """Run the full conversation pipeline for a user message (thin transport — delegates every step)."""
    with _observe("POST /chat/message"):
        if not body.message or not body.message.strip():
            raise HTTPException(status_code=400, detail="message is required")
        pipe = _pipeline(request)
        engine: ConversationEngine = pipe["engine"]

        if body.session_id:
            try:
                engine.get_session(body.session_id)
            except SessionNotFoundError:
                raise HTTPException(status_code=404, detail=f"session {body.session_id!r} not found")
            session_id = body.session_id
        else:
            session_id = engine.create_session().session_id

        try:
            orchestration = engine.handle_message(session_id, body.message)
        except DuplicateMessageError:
            raise HTTPException(status_code=409, detail="duplicate consecutive message")
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        if orchestration.next_step is NextStep.RETRIEVAL and orchestration.intent is not None:
            retrieval = pipe["orchestrator"].retrieve(orchestration.intent)
            prompt = pipe["prompt_builder"].build(retrieval=retrieval, user_request=body.message)
            llm = pipe["llm"].generate(LLMRequest(prompt=prompt.render(), model=_MODEL))
            if not llm.ok:
                raise HTTPException(status_code=_HTTP_FROM_LLM.get(llm.error.category, 500),
                                    detail=f"llm {llm.error.category.value}")
            text = llm.text
            availability = retrieval.availability.value
            citations = [c.stable_dict() for c in retrieval.citations]
        else:                                               # CLARIFY — no retrieval / LLM
            text = _CLARIFY.get(orchestration.follow_up, "Could you rephrase?")
            availability = "NOT_AVAILABLE"
            citations = []

        return ChatMessageResponse(
            response=text, session_id=session_id, conversation_status=orchestration.state.value,
            availability_status=availability, citations=citations,
            intent=orchestration.intent.intent.value if orchestration.intent else None,
            next_step=orchestration.next_step.value, versions=_versions(),
        )


@router.post("/session", response_model=SessionResponse)
async def create_session(body: SessionCreateRequest, request: Request) -> SessionResponse:
    """Create a new conversation session."""
    with _observe("POST /chat/session"):
        session = _pipeline(request)["engine"].create_session(title=body.title)
        return _session_response(session, "CREATED")


@router.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, request: Request) -> SessionResponse:
    """The session's message history + lifecycle state."""
    with _observe("GET /chat/session/{id}"):
        engine = _pipeline(request)["engine"]
        try:
            session = engine.get_session(session_id)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")
        return _session_response(session, engine.state_of(session_id).value)


@router.delete("/session/{session_id}", response_model=SessionResponse)
async def delete_session(session_id: str, request: Request) -> SessionResponse:
    """Close a session (terminal). The legacy chat is untouched."""
    with _observe("DELETE /chat/session/{id}"):
        engine = _pipeline(request)["engine"]
        try:
            engine.close_session(session_id)
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")
        return _session_response(engine.get_session(session_id), engine.state_of(session_id).value)


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Aggregate readiness of the conversation components (no business logic executed)."""
    with _observe("GET /chat/health"):
        state = request.app.state
        forward_store = getattr(state, "forward_store", None)
        retrieval = getattr(state, "retrieval", None)
        deps_ready = forward_store is not None and retrieval is not None
        llm = getattr(state, "conversation_llm", None) or create_adapter("echo")
        components = {
            "conversation_engine": {"ready": deps_ready},
            "retrieval": {"ready": deps_ready},
            "prompt_builder": {"ready": True},
            "llm_adapter": llm.health(),
        }
        status = "ready" if deps_ready else "degraded"
        return HealthResponse(status=status, components=components, versions=_versions())


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """The conversation-stack versions."""
    with _observe("GET /chat/version"):
        return VersionResponse(api_version=API_VERSION, versions=_versions())


def _session_response(session: Any, status: str) -> SessionResponse:
    return SessionResponse(session_id=session.session_id, status=status,
                           message_count=session.message_count,
                           messages=[m.to_dict() for m in session.messages], versions=_versions())
