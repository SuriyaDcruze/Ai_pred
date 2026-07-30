"""Tests for the LLM Adapter (Sprint 6 · Milestone 6).

Cover the provider abstraction, factory/registry behaviour, request validation, response
normalization, serialization (latency excluded from the fingerprint), deterministic error mapping,
health + version reporting, deterministic behaviour, and that the adapter imports no LLM SDK or
other engine. Pure infrastructure — no retrieval/intent/prompt/session.
"""

from __future__ import annotations

import pytest

from app.conversation.llm_adapter import (
    LLM_ADAPTER_VERSION,
    EchoProvider,
    FinishReason,
    InvalidLLMRequestError,
    LLMAdapter,
    LLMErrorCategory,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    OpenAIProvider,
    ProviderNotFoundError,
    available_providers,
    create_adapter,
    register_provider,
)


def _req(**kw):
    kw.setdefault("prompt", "explain the decision")
    kw.setdefault("model", "gpt-x")
    return LLMRequest(**kw)


# --------------------------------------------------------------- request validation
def test_request_validation():
    assert _req().model == "gpt-x"
    with pytest.raises(InvalidLLMRequestError):
        LLMRequest(prompt="   ", model="m")
    with pytest.raises(InvalidLLMRequestError):
        LLMRequest(prompt="p", model="")
    with pytest.raises(InvalidLLMRequestError):
        LLMRequest(prompt="p", model="m", temperature=3.0)
    with pytest.raises(InvalidLLMRequestError):
        LLMRequest(prompt="p", model="m", max_tokens=0)


# --------------------------------------------------------------- echo provider / adapter
def test_echo_provider_generates_deterministically():
    adapter = create_adapter("echo")
    a = adapter.generate(_req())
    b = adapter.generate(_req())
    assert a.ok and a.finish_reason is FinishReason.STOP and a.provider == "echo"
    assert a.token_usage["total_tokens"] > 0
    assert a.stable_dict() == b.stable_dict() and a.checksum == b.checksum


def test_adapter_rejects_non_request():
    with pytest.raises(InvalidLLMRequestError):
        create_adapter("echo").generate({"prompt": "x"})   # type: ignore[arg-type]


# --------------------------------------------------------------- factory / registry
def test_unknown_provider_rejected():
    with pytest.raises(ProviderNotFoundError):
        create_adapter("does-not-exist")


def test_register_custom_provider():
    class _Fixed(LLMProvider):
        name = "fixed"
        version = "fixed-1"

        def generate(self, request):
            return LLMResponse(text="fixed", finish_reason=FinishReason.STOP,
                               token_usage={"total_tokens": 1}, provider=self.name,
                               provider_version=self.version)

    register_provider("fixed", lambda **cfg: _Fixed())
    assert "fixed" in available_providers()
    assert create_adapter("fixed").generate(_req()).text == "fixed"


# --------------------------------------------------------------- openai provider (duck-typed client)
class _FakeClient:
    def complete(self, request):
        return {"text": "hello", "finish_reason": "length",
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                "metadata": {"id": "abc"}, "latency_ms": 12.5}


def test_openai_provider_normalises_response():
    resp = OpenAIProvider(_FakeClient()).generate(_req())
    assert resp.ok and resp.text == "hello" and resp.finish_reason is FinishReason.LENGTH
    assert resp.token_usage["total_tokens"] == 6 and resp.provider == "openai"
    assert resp.provider_metadata == {"id": "abc"}


def test_openai_provider_without_client_is_unavailable():
    resp = OpenAIProvider(None).generate(_req())
    assert not resp.ok and resp.error.category is LLMErrorCategory.PROVIDER_UNAVAILABLE


# --------------------------------------------------------------- error normalization
@pytest.mark.parametrize("exc_name,category", [
    ("RateLimitError", LLMErrorCategory.RATE_LIMITED),
    ("AuthenticationError", LLMErrorCategory.AUTHENTICATION_ERROR),
    ("APITimeoutError", LLMErrorCategory.TIMEOUT),
    ("APIConnectionError", LLMErrorCategory.PROVIDER_UNAVAILABLE),
    ("InvalidRequestError", LLMErrorCategory.INVALID_REQUEST),
    ("SomethingElse", LLMErrorCategory.INTERNAL_ERROR),
])
def test_error_mapping_is_deterministic(exc_name, category):
    exc_type = type(exc_name, (Exception,), {})

    class _Raiser:
        def complete(self, request):
            raise exc_type("boom")

    resp = OpenAIProvider(_Raiser()).generate(_req())
    assert not resp.ok and resp.finish_reason is FinishReason.ERROR
    assert resp.error.category is category and resp.error.provider == "openai"


# --------------------------------------------------------------- health / version
def test_health_and_version():
    adapter = create_adapter("echo")
    h = adapter.health()
    assert h["adapter_version"] == LLM_ADAPTER_VERSION and h["available"] is True
    v = adapter.version()
    assert v == {"adapter_version": LLM_ADAPTER_VERSION, "provider": "echo",
                 "provider_version": "echo-1", "api_version": None}


def test_azure_provider_registered():
    adapter = create_adapter("azure_openai", client=_FakeClient())
    assert adapter.version()["provider"] == "azure_openai"
    assert adapter.generate(_req()).text == "hello"


# --------------------------------------------------------------- serialization
def test_response_serialization_excludes_latency():
    resp = OpenAIProvider(_FakeClient()).generate(_req())
    assert "latency_ms" not in resp.stable_dict() and "latency_ms" in resp.to_dict()
    assert resp.to_dict()["latency_ms"] == 12.5


def test_request_round_trip():
    r = _req(temperature=0.5, max_tokens=256, system_instructions="sys", metadata={"k": "v"})
    assert LLMRequest.from_dict(r.to_dict()) == r


# --------------------------------------------------------------- isolation
def test_llm_adapter_imports_no_sdk_or_engine():
    import ast

    import app.conversation.llm_adapter as la
    with open(la.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("openai", "app.ai.sklearn_model", "app.ai.outcome_model", "app.decision_intelligence",
                 "app.conversation.retrieval", "app.conversation.intent", "app.conversation.prompt",
                 "app.conversation.engine")
    for name in imported:
        assert not name.startswith(forbidden), f"LLM adapter must not import {name}"
