"""LLM Planning Adapter for the Agent Engine (Sprint 7 · Milestone 6).

A **provider-independent** adapter that turns an :class:`~app.agent.models.AgentTask` (plus the
available tool metadata and planning constraints) into a **structured planning *suggestion***. The
LLM is **advisory only**: its output is an untrusted suggestion that must be validated by the
deterministic Planner (M3) before it can ever influence execution — this module never generates an
executable plan, executes tools, evaluates permissions, or invokes an engine.

Consumers depend only on the interface (:class:`PlanningLLMAdapter` / :class:`PlanningLLMProvider`),
so a provider (OpenAI, Azure OpenAI, a local model) can be added without changing consumer code. No
provider SDK is imported (providers take a duck-typed client). Provider faults are normalised into
deterministic categories, provider output is schema-validated, and requests/responses serialise
deterministically (volatile latency excluded from the fingerprint). It imports **no** engine and
**not** the Planner — it only produces suggestions the Planner will independently validate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from app.agent.models import AgentTask, _checksum, _get

#: The LLM Planning Adapter method/schema version. A shape/method change is a new version.
PLANNING_ADAPTER_VERSION: str = "planllm-1"


# --------------------------------------------------------------------------- enums
class PlanningLLMErrorCategory(str, Enum):
    """Deterministic, provider-agnostic planning-adapter error categories."""

    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# --------------------------------------------------------------------------- errors
class PlanningLLMError(Exception):
    """Base class for adapter-level errors (raised only for programmer errors, not provider faults)."""


class InvalidPlanningRequestError(PlanningLLMError):
    """The planning request is malformed."""


class InvalidPlanningResponseError(PlanningLLMError):
    """The provider's suggestion failed schema / tool validation."""


class PlanningProviderNotFoundError(PlanningLLMError):
    """No planning provider registered under the requested name."""


def _tokens(text: str) -> int:
    return max(0, (len(text) + 3) // 4)


# --------------------------------------------------------------------------- request
@dataclass(frozen=True)
class PlanningRequest:
    """An advisory planning request. Carries the task, the tool ids the model may choose from, the
    read-only planning context / constraints, and model configuration. **No engine data is passed** —
    only agent-domain metadata. Construction validates it."""

    task: AgentTask
    available_tools: tuple[str, ...] = ()
    planning_context: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    model: str = "stub-planner"
    temperature: float = 0.0
    max_tokens: int = 512
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.task, AgentTask) or not self.task.task_id:
            raise InvalidPlanningRequestError("a valid AgentTask is required")
        if not isinstance(self.model, str) or not self.model:
            raise InvalidPlanningRequestError("model identifier is required")
        if not 0.0 <= float(self.temperature) <= 2.0:
            raise InvalidPlanningRequestError("temperature must be in [0, 2]")
        if not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise InvalidPlanningRequestError("max_tokens must be a positive integer")
        for tid in self.available_tools:
            if not isinstance(tid, str) or not tid:
                raise InvalidPlanningRequestError("available_tools must be non-empty strings")
        if len(set(self.available_tools)) != len(self.available_tools):
            raise InvalidPlanningRequestError("available_tools must be unique")

    @classmethod
    def create(cls, *, task: AgentTask, available_tools: "tuple[str, ...] | list[str]" = (),
               planning_context: dict[str, Any] | None = None,
               constraints: dict[str, Any] | None = None, model: str = "stub-planner",
               temperature: float = 0.0, max_tokens: int = 512,
               metadata: dict[str, Any] | None = None) -> "PlanningRequest":
        return cls(task=task, available_tools=tuple(available_tools),
                   planning_context=planning_context or {}, constraints=constraints or {},
                   model=model, temperature=temperature, max_tokens=max_tokens,
                   metadata=metadata or {})

    def stable_dict(self) -> dict[str, Any]:
        return {"task": self.task.stable_dict(), "available_tools": list(self.available_tools),
                "planning_context": self.planning_context, "constraints": self.constraints,
                "model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens,
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "task": self.task.to_dict()}

    @property
    def checksum(self) -> str:
        return _checksum(self.stable_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanningRequest":
        return cls(task=AgentTask.from_dict(_get(data, "task") or {}),
                   available_tools=tuple(_get(data, "available_tools") or ()),
                   planning_context=dict(_get(data, "planning_context") or {}),
                   constraints=dict(_get(data, "constraints") or {}),
                   model=_get(data, "model", "stub-planner"),
                   temperature=float(_get(data, "temperature", 0.0)),
                   max_tokens=int(_get(data, "max_tokens", 512)),
                   metadata=dict(_get(data, "metadata") or {}))


# --------------------------------------------------------------------------- response
@dataclass(frozen=True)
class SuggestedStep:
    """One advisory suggested step — a tool id and (advisory) dependencies on other suggested tools.
    This is *not* an execution step; the Planner produces the authoritative one."""

    tool_id: str
    depends_on: tuple[str, ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, str) or not self.tool_id:
            raise InvalidPlanningResponseError("suggested step tool_id must be a non-empty string")

    def stable_dict(self) -> dict[str, Any]:
        return {"tool_id": self.tool_id, "depends_on": list(self.depends_on), "note": self.note}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SuggestedStep":
        return cls(tool_id=_get(data, "tool_id", ""),
                   depends_on=tuple(_get(data, "depends_on") or ()), note=_get(data, "note"))


@dataclass(frozen=True)
class PlanningProviderError:
    """A normalised provider error (never a leaked provider-specific exception)."""

    category: PlanningLLMErrorCategory
    message: str
    provider: str

    def stable_dict(self) -> dict[str, Any]:
        return {"category": self.category.value, "message": self.message, "provider": self.provider}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanningProviderError":
        return cls(category=PlanningLLMErrorCategory(_get(data, "category")),
                   message=_get(data, "message", ""), provider=_get(data, "provider", ""))


@dataclass(frozen=True)
class PlanningResponse:
    """A normalised, structured planning **suggestion** — the single shape every provider maps into.
    It is advisory: it never becomes an executable plan without the deterministic Planner. `confidence`
    is provider-supplied metadata only; `latency_ms` is runtime-only and excluded from the checksum."""

    suggested_tools: tuple[SuggestedStep, ...]
    rationale: str
    provider: str
    provider_version: str
    planning_notes: tuple[str, ...] = ()
    confidence: float | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    error: PlanningProviderError | None = None
    latency_ms: float | None = None                # metadata only — excluded from the fingerprint
    version: str = PLANNING_ADAPTER_VERSION

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def tool_ids(self) -> tuple[str, ...]:
        return tuple(s.tool_id for s in self.suggested_tools)

    def as_requested_tools(self) -> tuple[tuple[str, ...], dict[str, list[str]]]:
        """Advisory (tool_ids, dependencies) the caller may feed to the **Planner** (which remains the
        authority). This is not an executable plan — the Planner validates and orders it."""
        deps = {s.tool_id: list(s.depends_on) for s in self.suggested_tools if s.depends_on}
        return self.tool_ids, deps

    def stable_dict(self) -> dict[str, Any]:
        return {"suggested_tools": [s.stable_dict() for s in self.suggested_tools],
                "rationale": self.rationale, "planning_notes": list(self.planning_notes),
                "confidence": self.confidence, "provider": self.provider,
                "provider_version": self.provider_version,
                "provider_metadata": self.provider_metadata,
                "error": self.error.stable_dict() if self.error else None, "version": self.version}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "latency_ms": self.latency_ms}

    @property
    def checksum(self) -> str:
        return _checksum(self.stable_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanningResponse":
        error = _get(data, "error")
        return cls(
            suggested_tools=tuple(SuggestedStep.from_dict(s) for s in (_get(data, "suggested_tools") or [])),
            rationale=_get(data, "rationale", ""),
            provider=_get(data, "provider", ""), provider_version=_get(data, "provider_version", ""),
            planning_notes=tuple(_get(data, "planning_notes") or ()),
            confidence=_get(data, "confidence"),
            provider_metadata=dict(_get(data, "provider_metadata") or {}),
            error=PlanningProviderError.from_dict(error) if error else None,
            latency_ms=_get(data, "latency_ms"),
            version=_get(data, "version", PLANNING_ADAPTER_VERSION))


# --------------------------------------------------------------------------- validation
def validate_planning_response(response: PlanningResponse, request: PlanningRequest) -> PlanningResponse:
    """Validate a provider suggestion against the request — schema, tool-id validity, duplicates,
    unsupported tools, malformed dependencies. Raises :class:`InvalidPlanningResponseError`."""
    if response.error is not None:
        return response
    ids = [s.tool_id for s in response.suggested_tools]
    if len(ids) != len(set(ids)):
        raise InvalidPlanningResponseError(f"duplicate suggested tools: {ids}")
    allowed = set(request.available_tools)
    id_set = set(ids)
    for step in response.suggested_tools:
        if allowed and step.tool_id not in allowed:
            raise InvalidPlanningResponseError(f"unsupported tool suggested: {step.tool_id}")
        for dep in step.depends_on:
            if dep not in id_set:
                raise InvalidPlanningResponseError(
                    f"suggested step {step.tool_id} depends on non-suggested {dep}")
    return response


# --------------------------------------------------------------------------- providers
class PlanningLLMProvider(ABC):
    """A provider-independent planning backend. Add a provider by implementing this — consumers never
    change. A provider fault must be returned as an errored response, never raised."""

    name: str = "provider"
    version: str = "provider-0"

    @abstractmethod
    def suggest(self, request: PlanningRequest) -> PlanningResponse:  # pragma: no cover - interface
        ...

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "provider_version": self.version, "available": True}

    def api_version(self) -> str | None:
        return None


class EchoPlanningProvider(PlanningLLMProvider):
    """A deterministic, offline stub provider (the concrete implementation for testing / no-LLM
    deployments). It suggests the available tools in a stable order with no dependencies, clearly
    marked as a stub — no network, no API key, no engine."""

    name = "echo"
    version = "echo-planner-1"

    def suggest(self, request: PlanningRequest) -> PlanningResponse:
        steps = tuple(SuggestedStep(tool_id=tid) for tid in sorted(request.available_tools))
        rationale = ("[stub-planner — no live LLM configured] Suggesting the available tools in a "
                     "stable order; the deterministic Planner remains the authority.")
        return PlanningResponse(suggested_tools=steps, rationale=rationale,
                                planning_notes=("stub", f"task={request.task.task_id}"),
                                confidence=None, provider=self.name, provider_version=self.version,
                                provider_metadata={"stub": True}, latency_ms=0.0)


def _normalise_error(exc: Exception) -> PlanningLLMErrorCategory:
    """Map any provider exception to a deterministic category by type name (no SDK import)."""
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return PlanningLLMErrorCategory.RATE_LIMITED
    if "timeout" in name:
        return PlanningLLMErrorCategory.TIMEOUT
    if "unavailable" in name or "connection" in name or "auth" in name or "permission" in name:
        return PlanningLLMErrorCategory.PROVIDER_UNAVAILABLE
    if "invalid" in name or "badrequest" in name:
        return PlanningLLMErrorCategory.INVALID_REQUEST
    return PlanningLLMErrorCategory.INTERNAL_ERROR


class OpenAIPlanningProvider(PlanningLLMProvider):
    """OpenAI / Azure OpenAI planning provider — designed against a **duck-typed client** so no SDK is
    imported (a thin translator supplies ``client.suggest(request) -> dict``). Any client exception is
    normalised into an errored response."""

    def __init__(self, client: Any = None, *, name: str = "openai", version: str = "openai-planner-1",
                 api_version: str | None = None) -> None:
        self._client = client
        self.name = name
        self.version = version
        self._api_version = api_version

    def suggest(self, request: PlanningRequest) -> PlanningResponse:
        if self._client is None:
            return self._error(PlanningLLMErrorCategory.PROVIDER_UNAVAILABLE, "no client configured")
        try:
            result = self._client.suggest(request)
        except Exception as exc:                        # noqa: BLE001 — normalise, never leak
            return self._error(_normalise_error(exc), str(exc))
        try:
            steps = tuple(
                SuggestedStep(tool_id=str(s.get("tool_id", "")),
                              depends_on=tuple(s.get("depends_on") or ()), note=s.get("note"))
                for s in (result.get("suggested_tools") or []))
        except InvalidPlanningResponseError as exc:
            return self._error(PlanningLLMErrorCategory.INVALID_RESPONSE, str(exc))
        return PlanningResponse(
            suggested_tools=steps, rationale=str(result.get("rationale", "")),
            planning_notes=tuple(result.get("planning_notes") or ()),
            confidence=result.get("confidence"), provider=self.name, provider_version=self.version,
            provider_metadata=dict(result.get("metadata") or {}), latency_ms=result.get("latency_ms"))

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "provider_version": self.version,
                "available": self._client is not None}

    def api_version(self) -> str | None:
        return self._api_version

    def _error(self, category: PlanningLLMErrorCategory, message: str) -> PlanningResponse:
        return PlanningResponse(suggested_tools=(), rationale="", provider=self.name,
                                provider_version=self.version,
                                error=PlanningProviderError(category, message, self.name))


# --------------------------------------------------------------------------- registry / factory
#: Planning-provider factories keyed by name — add one by registering a factory (consumers unchanged).
PLANNING_PROVIDER_FACTORIES: dict[str, Callable[..., PlanningLLMProvider]] = {
    "echo": lambda **cfg: EchoPlanningProvider(),
    "openai": lambda **cfg: OpenAIPlanningProvider(cfg.get("client"), name="openai",
                                                   api_version=cfg.get("api_version")),
    "azure_openai": lambda **cfg: OpenAIPlanningProvider(
        cfg.get("client"), name="azure_openai", version="azure-openai-planner-1",
        api_version=cfg.get("api_version")),
}


def register_planning_provider(name: str, factory: Callable[..., PlanningLLMProvider]) -> None:
    """Register a planning-provider factory (extensibility point — no consumer change required)."""
    PLANNING_PROVIDER_FACTORIES[name] = factory


def available_planning_providers() -> list[str]:
    return list(PLANNING_PROVIDER_FACTORIES)


# --------------------------------------------------------------------------- the adapter
class PlanningLLMAdapter:
    """The consumer-facing, provider-independent planning interface: `suggest` / `health` / `version`.
    It validates the request, delegates to the provider, then validates the suggestion — an invalid
    suggestion is returned as a normalised ``INVALID_RESPONSE`` error, never leaked or executed."""

    def __init__(self, provider: PlanningLLMProvider) -> None:
        self._provider = provider

    def suggest(self, request: PlanningRequest) -> PlanningResponse:
        if not isinstance(request, PlanningRequest):
            raise InvalidPlanningRequestError("expected a PlanningRequest")
        response = self._provider.suggest(request)
        if response.error is not None:
            return response
        try:
            validate_planning_response(response, request)
        except InvalidPlanningResponseError as exc:
            return PlanningResponse(
                suggested_tools=(), rationale=response.rationale, provider=response.provider,
                provider_version=response.provider_version,
                error=PlanningProviderError(PlanningLLMErrorCategory.INVALID_RESPONSE, str(exc),
                                            response.provider))
        return response

    def health(self) -> dict[str, Any]:
        return {"adapter_version": PLANNING_ADAPTER_VERSION, **self._provider.health()}

    def version(self) -> dict[str, Any]:
        return {"adapter_version": PLANNING_ADAPTER_VERSION, "provider": self._provider.name,
                "provider_version": self._provider.version,
                "api_version": self._provider.api_version()}


def create_planning_adapter(provider: str = "echo", **config: Any) -> PlanningLLMAdapter:
    """Build a :class:`PlanningLLMAdapter` for a registered provider (the provider factory).

    Raises:
        PlanningProviderNotFoundError: no provider registered under ``provider``.
    """
    if provider not in PLANNING_PROVIDER_FACTORIES:
        raise PlanningProviderNotFoundError(
            f"unknown planning provider {provider!r}; known: {', '.join(PLANNING_PROVIDER_FACTORIES)}")
    return PlanningLLMAdapter(PLANNING_PROVIDER_FACTORIES[provider](**config))
