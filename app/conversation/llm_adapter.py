"""LLM Adapter for the Conversation Intelligence Engine (Sprint 6 · Milestone 6).

A **provider-independent** infrastructure abstraction for invoking large language models. It accepts
a completed prompt (rendered by Milestone 4) and returns a **normalised** response. It performs
**model communication only**: it does not retrieve data, classify intents, build prompts, or manage
conversations — all orchestration stays outside the adapter.

Consumers depend only on the interface (:class:`LLMAdapter` / :class:`LLMProvider`), so a new provider
(OpenAI, Azure OpenAI, a local model) can be added without changing consumer code. Provider errors
are normalised into deterministic categories (never leaked as provider-specific exceptions), and
requests/responses serialise deterministically (volatile latency excluded from the fingerprint).

**Distinct from** the legacy `app/chat/llm.py` (`LLMAssistant`). This module imports **no** LLM SDK
(providers take a duck-typed client) and no other engine, so it is pure infrastructure.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

#: The LLM Adapter method/schema version.
LLM_ADAPTER_VERSION: str = "llm-1"


# --------------------------------------------------------------------------- enums / errors
class FinishReason(str, Enum):
    """Why generation stopped (normalised across providers)."""

    STOP = "STOP"
    LENGTH = "LENGTH"
    CONTENT_FILTER = "CONTENT_FILTER"
    ERROR = "ERROR"


class LLMErrorCategory(str, Enum):
    """Deterministic, provider-agnostic error categories."""

    INVALID_REQUEST = "INVALID_REQUEST"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class LLMAdapterError(Exception):
    """Base class for adapter-level errors (raised only for programmer errors, not provider faults)."""


class InvalidLLMRequestError(LLMAdapterError):
    """The request is malformed (bad prompt / model / temperature / max_tokens)."""


class ProviderNotFoundError(LLMAdapterError):
    """No provider registered under the requested name."""


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _tokens(text: str) -> int:
    return max(0, (len(text) + 3) // 4)


# --------------------------------------------------------------------------- models
@dataclass(frozen=True)
class LLMRequest:
    """A normalised generation request. Construction validates it (rejects invalid requests)."""

    prompt: str
    model: str
    system_instructions: str | None = None
    temperature: float = 0.0
    max_tokens: int = 512
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise InvalidLLMRequestError("prompt must be a non-empty string")
        if not isinstance(self.model, str) or not self.model:
            raise InvalidLLMRequestError("model identifier is required")
        if not 0.0 <= float(self.temperature) <= 2.0:
            raise InvalidLLMRequestError("temperature must be in [0, 2]")
        if not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise InvalidLLMRequestError("max_tokens must be a positive integer")

    def stable_dict(self) -> dict[str, Any]:
        return {"prompt": self.prompt, "model": self.model,
                "system_instructions": self.system_instructions, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "metadata": self.metadata}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LLMRequest":
        return cls(prompt=data.get("prompt", ""), model=data.get("model", ""),
                   system_instructions=data.get("system_instructions"),
                   temperature=float(data.get("temperature", 0.0)),
                   max_tokens=int(data.get("max_tokens", 512)), metadata=dict(data.get("metadata") or {}))


@dataclass(frozen=True)
class LLMError:
    """A normalised provider error (never a leaked provider-specific exception)."""

    category: LLMErrorCategory
    message: str
    provider: str

    def stable_dict(self) -> dict[str, Any]:
        return {"category": self.category.value, "message": self.message, "provider": self.provider}


@dataclass(frozen=True)
class LLMResponse:
    """A normalised generation response — the single shape every provider maps into."""

    text: str
    finish_reason: FinishReason
    token_usage: dict[str, int]
    provider: str
    provider_version: str
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    error: LLMError | None = None
    latency_ms: float | None = None            # metadata only — excluded from the fingerprint
    version: str = LLM_ADAPTER_VERSION

    @property
    def ok(self) -> bool:
        return self.error is None

    def stable_dict(self) -> dict[str, Any]:
        """Deterministic content (excludes volatile `latency_ms`)."""
        return {"text": self.text, "finish_reason": self.finish_reason.value,
                "token_usage": self.token_usage, "provider": self.provider,
                "provider_version": self.provider_version, "provider_metadata": self.provider_metadata,
                "error": self.error.stable_dict() if self.error else None, "version": self.version}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "latency_ms": self.latency_ms}

    def serialize(self) -> str:
        return json.dumps(self.stable_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def checksum(self) -> str:
        return _sha256(self.stable_dict())


# --------------------------------------------------------------------------- providers
class LLMProvider(ABC):
    """A provider-independent LLM backend. Add a provider by implementing this — consumers never change."""

    name: str = "provider"
    version: str = "provider-0"

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a response for a request. Provider faults are returned as an errored response,
        never raised."""

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "provider_version": self.version, "available": True}

    def api_version(self) -> str | None:
        return None


class EchoProvider(LLMProvider):
    """A deterministic, offline stub provider — the concrete implementation for testing / no-LLM
    deployments. It returns a clearly-marked stub response (no network, no API key)."""

    name = "echo"
    version = "echo-1"

    def generate(self, request: LLMRequest) -> LLMResponse:
        text = ("[stub-llm — no live LLM configured] Received a prompt for model "
                f"{request.model!r}. Configure a real LLM provider to produce explanations.")
        usage = {"prompt_tokens": _tokens((request.system_instructions or "") + request.prompt),
                 "completion_tokens": _tokens(text)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return LLMResponse(text=text, finish_reason=FinishReason.STOP, token_usage=usage,
                           provider=self.name, provider_version=self.version,
                           provider_metadata={"stub": True}, latency_ms=0.0)


_FINISH = {"stop": FinishReason.STOP, "length": FinishReason.LENGTH,
           "content_filter": FinishReason.CONTENT_FILTER, "error": FinishReason.ERROR}


def _normalise_error(exc: Exception) -> LLMErrorCategory:
    """Map any provider exception to a deterministic category by its type name (no SDK import)."""
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return LLMErrorCategory.RATE_LIMITED
    if "auth" in name or "permission" in name:
        return LLMErrorCategory.AUTHENTICATION_ERROR
    if "timeout" in name:
        return LLMErrorCategory.TIMEOUT
    if "unavailable" in name or "connection" in name or "apiconnection" in name:
        return LLMErrorCategory.PROVIDER_UNAVAILABLE
    if "invalid" in name or "badrequest" in name:
        return LLMErrorCategory.INVALID_REQUEST
    return LLMErrorCategory.INTERNAL_ERROR


class OpenAIProvider(LLMProvider):
    """OpenAI / Azure OpenAI provider — designed against a **duck-typed client** so the OpenAI SDK is
    never imported here (a thin translator supplies `client.complete(request) -> dict`). Any client
    exception is normalised into an errored response."""

    def __init__(self, client: Any = None, *, name: str = "openai", version: str = "openai-1",
                 api_version: str | None = None) -> None:
        self._client = client
        self.name = name
        self.version = version
        self._api_version = api_version

    def generate(self, request: LLMRequest) -> LLMResponse:
        if self._client is None:
            return self._error(LLMErrorCategory.PROVIDER_UNAVAILABLE, "no client configured")
        try:
            result = self._client.complete(request)
        except Exception as exc:                        # noqa: BLE001 — normalise, never leak
            return self._error(_normalise_error(exc), str(exc))
        usage = dict(result.get("usage") or {})
        return LLMResponse(
            text=result.get("text", ""),
            finish_reason=_FINISH.get(str(result.get("finish_reason", "stop")).lower(), FinishReason.STOP),
            token_usage=usage, provider=self.name, provider_version=self.version,
            provider_metadata=dict(result.get("metadata") or {}), latency_ms=result.get("latency_ms"),
        )

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "provider_version": self.version,
                "available": self._client is not None}

    def api_version(self) -> str | None:
        return self._api_version

    def _error(self, category: LLMErrorCategory, message: str) -> LLMResponse:
        return LLMResponse(text="", finish_reason=FinishReason.ERROR, token_usage={},
                           provider=self.name, provider_version=self.version,
                           error=LLMError(category, message, self.name))


# --------------------------------------------------------------------------- registry / factory
#: Provider factories keyed by name — add a provider by registering a factory (consumers unchanged).
PROVIDER_FACTORIES: dict[str, Callable[..., LLMProvider]] = {
    "echo": lambda **cfg: EchoProvider(),
    "openai": lambda **cfg: OpenAIProvider(cfg.get("client"), name="openai",
                                           api_version=cfg.get("api_version")),
    "azure_openai": lambda **cfg: OpenAIProvider(cfg.get("client"), name="azure_openai",
                                                 version="azure-openai-1", api_version=cfg.get("api_version")),
}


def register_provider(name: str, factory: Callable[..., LLMProvider]) -> None:
    """Register a provider factory (extensibility point — no consumer change required)."""
    PROVIDER_FACTORIES[name] = factory


def available_providers() -> list[str]:
    return list(PROVIDER_FACTORIES)


# --------------------------------------------------------------------------- the adapter
class LLMAdapter:
    """The consumer-facing, provider-independent LLM interface: `generate` / `health` / `version`."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Validate the request and delegate to the provider (which normalises any fault)."""
        if not isinstance(request, LLMRequest):
            raise InvalidLLMRequestError("expected an LLMRequest")
        return self._provider.generate(request)

    def health(self) -> dict[str, Any]:
        return {"adapter_version": LLM_ADAPTER_VERSION, **self._provider.health()}

    def version(self) -> dict[str, Any]:
        return {"adapter_version": LLM_ADAPTER_VERSION, "provider": self._provider.name,
                "provider_version": self._provider.version, "api_version": self._provider.api_version()}


def create_adapter(provider: str = "echo", **config: Any) -> LLMAdapter:
    """Build an :class:`LLMAdapter` for a registered provider (the provider factory).

    Raises:
        ProviderNotFoundError: no provider registered under ``provider``.
    """
    if provider not in PROVIDER_FACTORIES:
        raise ProviderNotFoundError(f"unknown provider {provider!r}; known: {', '.join(PROVIDER_FACTORIES)}")
    return LLMAdapter(PROVIDER_FACTORIES[provider](**config))
