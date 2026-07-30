"""Tests for the Agent Tool Registry (Sprint 7 · Milestone 2).

Cover tool definition + schema construction/validation, registration, duplicate detection, lookup by
id and by category, listing + deterministic ordering, category discovery, the read-only default
catalog, serialization round-trips, versioning (tool-1), and no-engine imports. Metadata only — no
execution, planning, permissions, engine invocation, LLM, or REST.
"""

from __future__ import annotations

import pytest

from app.agent.tools import (
    DEFAULT_TOOL_VERSION,
    TOOL_REGISTRY_VERSION,
    DuplicateToolError,
    InvalidCategoryError,
    InvalidToolDefinitionError,
    InvalidToolSchemaError,
    ToolAvailability,
    ToolCapability,
    ToolCategory,
    ToolDefinition,
    ToolNotFoundError,
    ToolParameter,
    ToolRegistry,
    ToolSchema,
    UnsupportedToolRegistryVersionError,
    default_registry,
    default_tool_definitions,
)


def _tool(tool_id: str = "memory.get_history", category: ToolCategory = ToolCategory.MEMORY,
          **kw) -> ToolDefinition:
    return ToolDefinition.create(tool_id=tool_id, name=kw.pop("name", "T"), category=category,
                                 supported_engine=kw.pop("supported_engine", "memory"), **kw)


# --------------------------------------------------------------- schema / parameter
def test_parameter_validates_type_and_name():
    p = ToolParameter(name="prediction_id", type="string")
    assert p.required and ToolParameter.from_dict(p.to_dict()) == p
    with pytest.raises(InvalidToolSchemaError):
        ToolParameter(name="x", type="frobnicate")
    with pytest.raises(InvalidToolSchemaError):
        ToolParameter(name="", type="string")


def test_schema_rejects_duplicate_parameters():
    with pytest.raises(InvalidToolSchemaError):
        ToolSchema.create([ToolParameter(name="a", type="string"),
                           ToolParameter(name="a", type="integer")])
    s = ToolSchema.create([ToolParameter(name="a", type="string"),
                          ToolParameter(name="b", type="integer", required=False)])
    assert s.required_names == ("a",)


# --------------------------------------------------------------- tool definition
def test_definition_is_deterministic_and_checksummed():
    a = _tool()
    b = _tool()
    assert a.checksum == b.checksum and a.version == DEFAULT_TOOL_VERSION
    assert a.availability is ToolAvailability.AVAILABLE and a.capability is ToolCapability.READ_ONLY
    diff = _tool(description="different")
    assert diff.checksum != a.checksum


def test_definition_rejects_bad_id_and_missing_metadata():
    with pytest.raises(InvalidToolDefinitionError):
        _tool(tool_id="NotAValidId")                    # not <engine>.<action>
    with pytest.raises(InvalidToolDefinitionError):
        _tool(tool_id="memory.get", name="", )          # missing name
    with pytest.raises(InvalidToolDefinitionError):
        _tool(supported_engine="")                       # missing engine


def test_write_tool_must_require_permission():
    with pytest.raises(InvalidToolDefinitionError):
        _tool(capability=ToolCapability.WRITE, permission_required=False)
    ok = _tool(tool_id="memory.write", capability=ToolCapability.WRITE, permission_required=True)
    assert ok.capability is ToolCapability.WRITE and ok.permission_required


# --------------------------------------------------------------- registry: registration / dup
def test_register_and_duplicate_detection():
    reg = ToolRegistry.create()
    assert len(reg) == 0 and reg.version == TOOL_REGISTRY_VERSION
    reg = reg.register(_tool(tool_id="memory.get_history"))
    assert len(reg) == 1 and "memory.get_history" in reg
    with pytest.raises(DuplicateToolError):
        reg.register(_tool(tool_id="memory.get_history", name="dup"))


def test_registry_rejects_duplicate_ids_at_construction():
    with pytest.raises(DuplicateToolError):
        ToolRegistry(version=TOOL_REGISTRY_VERSION,
                     tools=(_tool(tool_id="a.b"), _tool(tool_id="a.b")))


# --------------------------------------------------------------- registry: lookup / filter / order
def test_lookup_and_category_filter():
    reg = ToolRegistry.create([
        _tool(tool_id="memory.get_history", category=ToolCategory.MEMORY),
        _tool(tool_id="system.health", category=ToolCategory.SYSTEM, supported_engine="system"),
        _tool(tool_id="system.version", category=ToolCategory.SYSTEM, supported_engine="system"),
    ])
    assert reg.get("system.health").tool_id == "system.health"
    with pytest.raises(ToolNotFoundError):
        reg.get("nope.missing")
    system = reg.by_category(ToolCategory.SYSTEM)
    assert [t.tool_id for t in system] == ["system.health", "system.version"]
    assert reg.by_engine("system") == system
    with pytest.raises(InvalidCategoryError):
        reg.by_category("SYSTEM")                        # not a ToolCategory


def test_deterministic_ordering_independent_of_insertion():
    ids = ["system.version", "memory.get_history", "conversation.explain"]
    forward = ToolRegistry.create([_tool(tool_id=i, supported_engine="e") for i in ids])
    reverse = ToolRegistry.create([_tool(tool_id=i, supported_engine="e") for i in reversed(ids)])
    assert forward.ids() == tuple(sorted(ids)) == reverse.ids()
    assert forward.checksum == reverse.checksum       # order-independent identity


def test_categories_discovery_in_enum_order():
    reg = ToolRegistry.create([
        _tool(tool_id="system.health", category=ToolCategory.SYSTEM, supported_engine="system"),
        _tool(tool_id="memory.get_history", category=ToolCategory.MEMORY),
    ])
    assert reg.categories() == (ToolCategory.MEMORY, ToolCategory.SYSTEM)


# --------------------------------------------------------------- default catalog
def test_default_registry_is_read_only_and_available():
    reg = default_registry()
    assert len(reg) == len(default_tool_definitions()) >= 7
    for tool in reg.list():
        assert tool.capability is ToolCapability.READ_ONLY
        assert tool.permission_required is False
        assert tool.availability is ToolAvailability.AVAILABLE
    assert reg.has("decision_intelligence.get") and reg.has("system.health")
    assert reg.by_category(ToolCategory.SYSTEM)  # health + version


# --------------------------------------------------------------- serialization / versioning
def test_registry_round_trip():
    reg = default_registry()
    got = ToolRegistry.from_dict(reg.to_dict())
    assert got.ids() == reg.ids() and got.checksum == reg.checksum
    assert got.stable_dict() == reg.stable_dict()


def test_definition_round_trip():
    tool = ToolDefinition.create(
        tool_id="decision_intelligence.get", name="DI", category=ToolCategory.DECISION_INTELLIGENCE,
        supported_engine="decision_intelligence",
        input_schema=ToolSchema.create([ToolParameter(name="prediction_id", type="string")]),
        output_schema=ToolSchema.create([ToolParameter(name="intelligence", type="object")]))
    assert ToolDefinition.from_dict(tool.to_dict()) == tool


def test_unsupported_registry_version_rejected():
    with pytest.raises(UnsupportedToolRegistryVersionError):
        ToolRegistry(version="tool-999")


# --------------------------------------------------------------- isolation
def test_tools_import_no_engine():
    import ast

    import app.agent.tools as m
    with open(m.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.decision_intelligence",
                 "app.conversation", "app.memory", "app.similarity", "app.learning",
                 "app.forward_testing", "app.chat", "openai")
    for name in imported:
        assert not name.startswith(forbidden), f"M2 must not import {name}"
    # M2 may reuse only the shared primitives from the M1 domain module.
    assert "app.agent.models" in imported
