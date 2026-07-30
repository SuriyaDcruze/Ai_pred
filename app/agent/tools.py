"""Tool Registry for the Agent Engine (Sprint 7 · Milestone 2).

The catalog of tools the Agent Engine may (in a later milestone) plan over and — under explicit
permission — execute. **This milestone is metadata only.** It *describes* the available tools, their
categories, schemas, capabilities, and availability, and provides a deterministic, extensible
registry to register / look up / list / filter them. It performs **no** execution, planning,
permission checks, engine invocation, LLM calls, or REST exposure.

Determinism: tool identifiers are canonical and immutable; every definition and the registry itself
carry a SHA-256 checksum over their stable content (volatile timestamps excluded); listings use a
deterministic order (by ``tool_id``); everything serialises round-trip. The module imports nothing
from any engine — only the shared primitives from :mod:`app.agent.models`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from app.agent.models import AgentError, _checksum, _get, _id, _utc_now_iso

#: The Tool Registry method/schema version. A shape/method change is a new version, never an edit.
TOOL_REGISTRY_VERSION: str = "tool-1"

#: The default per-tool version stamped on a definition when none is supplied.
DEFAULT_TOOL_VERSION: str = "1.0.0"

#: Canonical form of a tool identifier: ``<engine>.<action>`` — deterministic and immutable.
_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

#: The JSON-ish parameter types a tool schema may declare.
SCHEMA_TYPES: frozenset[str] = frozenset(
    {"string", "integer", "number", "boolean", "object", "array", "null"}
)


# --------------------------------------------------------------------------- enums
class ToolCategory(str, Enum):
    """A deterministic tool category. ``FUTURE_EXTENSIONS`` is the explicit bucket for tools that do
    not yet fit a canonical category — the set is extended by adding a member in a future version
    (never by mutating global state), keeping categories deterministic yet extensible."""

    CONVERSATION = "CONVERSATION"
    DECISION_INTELLIGENCE = "DECISION_INTELLIGENCE"
    MEMORY = "MEMORY"
    SIMILARITY = "SIMILARITY"
    LEARNING = "LEARNING"
    SYSTEM = "SYSTEM"
    FUTURE_EXTENSIONS = "FUTURE_EXTENSIONS"


class ToolCapability(str, Enum):
    """Whether a tool reads or writes. AEGIS tools are read-only by default; a ``WRITE`` tool must
    require permission (enforced at definition time)."""

    READ_ONLY = "READ_ONLY"
    WRITE = "WRITE"


class ToolAvailability(str, Enum):
    """A tool's deterministic availability in the catalog (metadata only — nothing is probed)."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    DEPRECATED = "DEPRECATED"
    EXPERIMENTAL = "EXPERIMENTAL"


# --------------------------------------------------------------------------- errors
class ToolError(AgentError):
    """Base class for every tool-registry error."""


class InvalidToolDefinitionError(ToolError):
    """A tool definition is missing required metadata or violates an invariant."""


class InvalidToolSchemaError(ToolError):
    """A tool input/output schema is malformed (bad type or duplicate parameter)."""


class InvalidCategoryError(ToolError):
    """A tool category is not a known :class:`ToolCategory`."""


class DuplicateToolError(ToolError):
    """A tool id was registered more than once."""


class ToolNotFoundError(ToolError):
    """A tool id is not present in the registry."""


class UnsupportedToolRegistryVersionError(ToolError):
    """A registry version this build does not support."""


# --------------------------------------------------------------------------- schema
@dataclass(frozen=True)
class ToolParameter:
    """One field of a tool's input/output schema — a name, a JSON-ish type, and whether it is
    required. Metadata only; no value is ever validated against it here."""

    name: str
    type: str
    required: bool = True
    description: str = ""
    default: Any = None

    def __post_init__(self) -> None:
        if not self.name:
            raise InvalidToolSchemaError("parameter name is required")
        if self.type not in SCHEMA_TYPES:
            raise InvalidToolSchemaError(f"unknown parameter type {self.type!r}")

    def stable_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "required": self.required,
                "description": self.description, "default": self.default}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolParameter":
        return cls(name=_get(data, "name", ""), type=_get(data, "type", "string"),
                   required=bool(_get(data, "required", True)), description=_get(data, "description", ""),
                   default=_get(data, "default"))


@dataclass(frozen=True)
class ToolSchema:
    """An ordered, deterministic set of parameters describing a tool's inputs or outputs. Parameter
    names must be unique (schema integrity); ordering is preserved verbatim."""

    parameters: tuple[ToolParameter, ...] = ()

    def __post_init__(self) -> None:
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise InvalidToolSchemaError(f"duplicate parameter names in schema: {names}")

    @classmethod
    def create(cls, parameters: "Iterable[ToolParameter] | None" = None) -> "ToolSchema":
        return cls(parameters=tuple(parameters or ()))

    @property
    def required_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.parameters if p.required)

    def stable_dict(self) -> dict[str, Any]:
        return {"parameters": [p.stable_dict() for p in self.parameters]}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolSchema":
        return cls(parameters=tuple(ToolParameter.from_dict(p) for p in (_get(data, "parameters") or [])))


# --------------------------------------------------------------------------- tool definition
def _definition_checksum(tool_id: str, name: str, description: str, category: ToolCategory,
                         input_schema: ToolSchema, output_schema: ToolSchema,
                         permission_required: bool, capability: ToolCapability,
                         supported_engine: str, version: str, availability: ToolAvailability,
                         metadata: dict[str, Any]) -> str:
    return _checksum({
        "tool_id": tool_id, "name": name, "description": description, "category": category.value,
        "input_schema": input_schema.stable_dict(), "output_schema": output_schema.stable_dict(),
        "permission_required": permission_required, "capability": capability.value,
        "supported_engine": supported_engine, "version": version,
        "availability": availability.value, "metadata": metadata,
    })


@dataclass(frozen=True)
class ToolDefinition:
    """The immutable metadata description of one tool. Carries its canonical id, human name,
    category, input/output schemas, permission requirement, read/write capability, the engine it is
    served by, its own version, and availability. A SHA-256 ``checksum`` fingerprints the stable
    content (``created_at`` excluded). **No behaviour** — this describes a tool, it never runs one."""

    tool_id: str
    name: str
    description: str
    category: ToolCategory
    input_schema: ToolSchema
    output_schema: ToolSchema
    permission_required: bool
    capability: ToolCapability
    supported_engine: str
    version: str
    availability: ToolAvailability
    checksum: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if not self.tool_id or not _TOOL_ID_RE.match(self.tool_id):
            raise InvalidToolDefinitionError(
                f"tool_id {self.tool_id!r} must match '<engine>.<action>' (lower_snake)")
        if not self.name:
            raise InvalidToolDefinitionError("name is required")
        if not isinstance(self.category, ToolCategory):
            raise InvalidCategoryError(f"invalid category {self.category!r}")
        if not isinstance(self.input_schema, ToolSchema) or not isinstance(self.output_schema, ToolSchema):
            raise InvalidToolSchemaError("input_schema and output_schema must be ToolSchema")
        if not isinstance(self.capability, ToolCapability):
            raise InvalidToolDefinitionError(f"invalid capability {self.capability!r}")
        if not isinstance(self.availability, ToolAvailability):
            raise InvalidToolDefinitionError(f"invalid availability {self.availability!r}")
        if not isinstance(self.permission_required, bool):
            raise InvalidToolDefinitionError("permission_required must be a bool")
        if not self.supported_engine:
            raise InvalidToolDefinitionError("supported_engine is required")
        if not self.version:
            raise InvalidToolDefinitionError("version is required")
        # A state-changing tool must require permission (the read-only-by-default invariant).
        if self.capability is ToolCapability.WRITE and not self.permission_required:
            raise InvalidToolDefinitionError("a WRITE tool must set permission_required=True")

    @classmethod
    def create(cls, *, tool_id: str, name: str, category: ToolCategory, supported_engine: str,
               description: str = "", input_schema: ToolSchema | None = None,
               output_schema: ToolSchema | None = None, permission_required: bool = False,
               capability: ToolCapability = ToolCapability.READ_ONLY, version: str = DEFAULT_TOOL_VERSION,
               availability: ToolAvailability = ToolAvailability.AVAILABLE,
               metadata: dict[str, Any] | None = None) -> "ToolDefinition":
        in_schema = input_schema or ToolSchema()
        out_schema = output_schema or ToolSchema()
        meta = metadata or {}
        checksum = _definition_checksum(tool_id, name, description, category, in_schema, out_schema,
                                        permission_required, capability, supported_engine, version,
                                        availability, meta)
        return cls(tool_id=tool_id, name=name, description=description, category=category,
                   input_schema=in_schema, output_schema=out_schema,
                   permission_required=permission_required, capability=capability,
                   supported_engine=supported_engine, version=version, availability=availability,
                   checksum=checksum, metadata=meta)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id, "name": self.name, "description": self.description,
            "category": self.category.value, "input_schema": self.input_schema.stable_dict(),
            "output_schema": self.output_schema.stable_dict(),
            "permission_required": self.permission_required, "capability": self.capability.value,
            "supported_engine": self.supported_engine, "version": self.version,
            "availability": self.availability.value, "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolDefinition":
        category = ToolCategory(_get(data, "category", ToolCategory.FUTURE_EXTENSIONS.value))
        in_schema = ToolSchema.from_dict(_get(data, "input_schema") or {})
        out_schema = ToolSchema.from_dict(_get(data, "output_schema") or {})
        permission_required = bool(_get(data, "permission_required", False))
        capability = ToolCapability(_get(data, "capability", ToolCapability.READ_ONLY.value))
        availability = ToolAvailability(_get(data, "availability", ToolAvailability.AVAILABLE.value))
        tool_id = _get(data, "tool_id")
        name = _get(data, "name", "")
        description = _get(data, "description", "")
        supported_engine = _get(data, "supported_engine", "")
        version = _get(data, "version", DEFAULT_TOOL_VERSION)
        metadata = dict(_get(data, "metadata") or {})
        checksum = _get(data, "checksum") or _definition_checksum(
            tool_id, name, description, category, in_schema, out_schema, permission_required,
            capability, supported_engine, version, availability, metadata)
        return cls(tool_id=tool_id, name=name, description=description, category=category,
                   input_schema=in_schema, output_schema=out_schema,
                   permission_required=permission_required, capability=capability,
                   supported_engine=supported_engine, version=version, availability=availability,
                   checksum=checksum, metadata=metadata, created_at=_get(data, "created_at"))


# --------------------------------------------------------------------------- registry (aggregate)
def _registry_checksum(version: str, tools: "tuple[ToolDefinition, ...]",
                       metadata: dict[str, Any]) -> str:
    return _checksum({"version": version, "registry_id": _id("registry", version),
                      "tools": [t.stable_dict() for t in tools], "metadata": metadata})


@dataclass(frozen=True)
class ToolRegistry:
    """An **immutable**, deterministic catalog of tool definitions. Registration is functional —
    :meth:`register` returns a *new* registry with a recomputed checksum. Duplicate ids are rejected;
    every listing is ordered by ``tool_id``. Metadata only: nothing here executes a tool."""

    version: str = TOOL_REGISTRY_VERSION
    tools: tuple[ToolDefinition, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""

    def __post_init__(self) -> None:
        if self.version != TOOL_REGISTRY_VERSION:
            raise UnsupportedToolRegistryVersionError(f"unsupported registry version {self.version!r}")
        ids = [t.tool_id for t in self.tools]
        if len(ids) != len(set(ids)):
            raise DuplicateToolError(f"duplicate tool ids in registry: {ids}")
        if self.tools != tuple(sorted(self.tools, key=lambda t: t.tool_id)):
            raise InvalidToolDefinitionError("registry tools must be ordered by tool_id")
        object.__setattr__(
            self, "checksum", _registry_checksum(self.version, self.tools, self.metadata))

    # ---- construction ----------------------------------------------------
    @classmethod
    def create(cls, tools: "Iterable[ToolDefinition] | None" = None, *,
               version: str = TOOL_REGISTRY_VERSION,
               metadata: dict[str, Any] | None = None) -> "ToolRegistry":
        registry = cls(version=version, tools=(), metadata=metadata or {})
        return registry.register_all(tools or ())

    # ---- functional registration ----------------------------------------
    def register(self, tool: ToolDefinition) -> "ToolRegistry":
        """Return a new registry with ``tool`` added. Rejects a conflicting id."""
        if any(t.tool_id == tool.tool_id for t in self.tools):
            raise DuplicateToolError(f"tool id already registered: {tool.tool_id}")
        ordered = tuple(sorted(self.tools + (tool,), key=lambda t: t.tool_id))
        return ToolRegistry(version=self.version, tools=ordered, metadata=self.metadata)

    def register_all(self, tools: "Iterable[ToolDefinition]") -> "ToolRegistry":
        registry = self
        for tool in tools:
            registry = registry.register(tool)
        return registry

    # ---- lookup / discovery ---------------------------------------------
    def has(self, tool_id: str) -> bool:
        return any(t.tool_id == tool_id for t in self.tools)

    def get(self, tool_id: str) -> ToolDefinition:
        for tool in self.tools:
            if tool.tool_id == tool_id:
                return tool
        raise ToolNotFoundError(f"tool not found: {tool_id}")

    def by_category(self, category: ToolCategory) -> tuple[ToolDefinition, ...]:
        if not isinstance(category, ToolCategory):
            raise InvalidCategoryError(f"invalid category {category!r}")
        return tuple(t for t in self.tools if t.category is category)

    def by_engine(self, supported_engine: str) -> tuple[ToolDefinition, ...]:
        return tuple(t for t in self.tools if t.supported_engine == supported_engine)

    def available(self) -> tuple[ToolDefinition, ...]:
        return tuple(t for t in self.tools if t.availability is ToolAvailability.AVAILABLE)

    def list(self) -> tuple[ToolDefinition, ...]:
        """Every tool, deterministically ordered by ``tool_id``."""
        return self.tools

    def ids(self) -> tuple[str, ...]:
        return tuple(t.tool_id for t in self.tools)

    def categories(self) -> tuple[ToolCategory, ...]:
        """The categories present, in canonical enum order."""
        present = {t.category for t in self.tools}
        return tuple(c for c in ToolCategory if c in present)

    def __len__(self) -> int:
        return len(self.tools)

    def __contains__(self, tool_id: object) -> bool:
        return isinstance(tool_id, str) and self.has(tool_id)

    # ---- serialization ---------------------------------------------------
    def stable_dict(self) -> dict[str, Any]:
        return {"version": self.version, "tools": [t.stable_dict() for t in self.tools],
                "metadata": self.metadata}

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "tools": [t.to_dict() for t in self.tools],
                "metadata": self.metadata, "checksum": self.checksum}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolRegistry":
        return cls.create(
            tools=[ToolDefinition.from_dict(t) for t in (_get(data, "tools") or [])],
            version=_get(data, "version", TOOL_REGISTRY_VERSION),
            metadata=dict(_get(data, "metadata") or {}),
        )


# --------------------------------------------------------------------------- default catalog
def default_tool_definitions() -> tuple[ToolDefinition, ...]:
    """The canonical read-only tool catalog describing (as **metadata only**) the capabilities the
    existing AEGIS engines expose. Every entry is read-only and needs no permission; none of these
    definitions imports or invokes an engine — they merely *describe* one."""

    pred_id = ToolParameter(name="prediction_id", type="string", description="Target prediction id.")
    symbol = ToolParameter(name="symbol", type="string", required=False, description="Instrument symbol.")
    return (
        ToolDefinition.create(
            tool_id="conversation.explain", name="Explain prediction",
            category=ToolCategory.CONVERSATION, supported_engine="conversation",
            description="Explain an existing prediction in natural language (explanation only).",
            input_schema=ToolSchema.create([
                ToolParameter(name="question", type="string", description="The user's question."),
                pred_id, symbol]),
            output_schema=ToolSchema.create([ToolParameter(name="answer", type="string")])),
        ToolDefinition.create(
            tool_id="decision_intelligence.get", name="Get decision intelligence",
            category=ToolCategory.DECISION_INTELLIGENCE, supported_engine="decision_intelligence",
            description="Fetch the composed, evidence-bound decision-intelligence object for a prediction.",
            input_schema=ToolSchema.create([pred_id]),
            output_schema=ToolSchema.create([ToolParameter(name="intelligence", type="object")])),
        ToolDefinition.create(
            tool_id="memory.get_history", name="Get prediction history",
            category=ToolCategory.MEMORY, supported_engine="memory",
            description="Retrieve historical prediction outcomes for a symbol.",
            input_schema=ToolSchema.create([symbol]),
            output_schema=ToolSchema.create([ToolParameter(name="history", type="array")])),
        ToolDefinition.create(
            tool_id="similarity.find_similar", name="Find similar predictions",
            category=ToolCategory.SIMILARITY, supported_engine="similarity",
            description="Find historically similar predictions for a given prediction.",
            input_schema=ToolSchema.create([pred_id]),
            output_schema=ToolSchema.create([ToolParameter(name="matches", type="array")])),
        ToolDefinition.create(
            tool_id="learning.get_summary", name="Get learning summary",
            category=ToolCategory.LEARNING, supported_engine="learning",
            description="Retrieve the deterministic learning summary derived from realised outcomes.",
            input_schema=ToolSchema.create([symbol]),
            output_schema=ToolSchema.create([ToolParameter(name="summary", type="object")])),
        ToolDefinition.create(
            tool_id="system.health", name="System health",
            category=ToolCategory.SYSTEM, supported_engine="system",
            description="Report aggregate read-only health of the AEGIS engines.",
            output_schema=ToolSchema.create([ToolParameter(name="status", type="string")])),
        ToolDefinition.create(
            tool_id="system.version", name="System version",
            category=ToolCategory.SYSTEM, supported_engine="system",
            description="Report the AEGIS platform and engine versions.",
            output_schema=ToolSchema.create([ToolParameter(name="version", type="string")])),
    )


def default_registry() -> ToolRegistry:
    """A :class:`ToolRegistry` seeded with the canonical read-only :func:`default_tool_definitions`."""
    return ToolRegistry.create(default_tool_definitions())
