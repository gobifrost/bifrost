"""Live capability classification shared by agent execution surfaces.

This module is deliberately database-free. ``resolve_agent_tools`` remains
responsible for resolving the live database catalog; this module classifies
only those resolved definitions (or an exact call name) against the grants
already loaded on an agent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid5

from src.services.execution.agent_helpers import (
    find_delegated_agent,
    parse_mcp_tool_name,
)
from src.services.llm import ToolDefinition
from src.services.tool_schema import validate_arguments_against_schema

GATEWAY_TOOL_NAMESPACE = UUID("bcf3f4d7-b95e-53cc-9b7f-35e7081d0d84")

CapabilitySource = Literal[
    "system",
    "knowledge",
    "workflow",
    "delegation",
    "external_mcp",
]


@dataclass(frozen=True)
class CapabilityRoute:
    """An exact, currently granted target for a callable capability."""

    source: CapabilitySource
    source_identity: str
    source_id: UUID | None = None
    remote_tool_name: str | None = None


@dataclass(frozen=True)
class CapabilityTool:
    """A resolved definition paired with its stable capability identity."""

    tool_ref: str
    definition: ToolDefinition
    source: CapabilitySource
    source_identity: str
    source_id: UUID | None = None
    remote_tool_name: str | None = None

    def compact(self) -> dict[str, Any]:
        return {
            "tool_ref": self.tool_ref,
            "name": self.definition.name,
            "description": self.definition.description,
            "source": self.source,
            "supports_async": self.source in {"workflow", "delegation"},
            "default_async": self.source == "delegation",
        }


class CapabilityArgumentError(Exception):
    """The live JSON schema rejected arguments or was itself invalid."""

    def __init__(
        self, *, issues: list[dict[str, Any]], schema_error: str | None
    ) -> None:
        super().__init__(
            schema_error or "Arguments do not match the live capability schema."
        )
        self.issues = issues
        self.schema_error = schema_error


def capability_ref(owner_id: UUID | str, route: CapabilityRoute) -> str:
    """Return the stable ref for a source identity within one agent boundary."""
    return str(uuid5(GATEWAY_TOOL_NAMESPACE, f"{owner_id}:{route.source_identity}"))


def resolve_capability_route(
    agent: Any,
    name: str,
    id_map: Mapping[str, UUID],
) -> CapabilityRoute | None:
    """Resolve one exact tool name against current, loaded agent grants.

    The caller receives ``None`` for unknown, revoked, or malformed names.
    There is intentionally no display-name or prefix fallback.
    """
    mcp_route = parse_mcp_tool_name(name)
    if mcp_route is not None:
        connection_id, remote_tool_name = mcp_route
        if id_map.get(name) != connection_id:
            return None
        connections = getattr(agent, "mcp_connections", None) or []
        if connection_id not in {connection.id for connection in connections}:
            return None
        return CapabilityRoute(
            source="external_mcp",
            source_identity=f"external_mcp:{connection_id}:{remote_tool_name}",
            source_id=connection_id,
            remote_tool_name=remote_tool_name,
        )

    system_tools = getattr(agent, "system_tools", None) or []
    if name == "search_knowledge" and (
        getattr(agent, "knowledge_sources", None) or name in system_tools
    ):
        return CapabilityRoute(
            source="knowledge",
            source_identity=f"knowledge:{name}",
        )

    if name in system_tools:
        return CapabilityRoute(
            source="system",
            source_identity=f"system:{name}",
        )

    delegated = find_delegated_agent(agent, name)
    if delegated is not None:
        return CapabilityRoute(
            source="delegation",
            source_identity=f"delegation:{delegated.id}",
            source_id=delegated.id,
        )

    workflow_id = id_map.get(name)
    if workflow_id is None:
        return None
    return CapabilityRoute(
        source="workflow",
        source_identity=f"workflow:{workflow_id}",
        source_id=workflow_id,
    )


def resolve_capability_tools(
    agent: Any,
    definitions: Sequence[ToolDefinition],
    id_map: Mapping[str, UUID],
) -> list[CapabilityTool]:
    """Classify live definitions that remain granted to the supplied agent."""
    tools: list[CapabilityTool] = []
    for definition in definitions:
        route = resolve_capability_route(agent, definition.name, id_map)
        if route is None:
            continue
        tools.append(
            CapabilityTool(
                tool_ref=capability_ref(agent.id, route),
                definition=definition,
                source=route.source,
                source_identity=route.source_identity,
                source_id=route.source_id,
                remote_tool_name=route.remote_tool_name,
            )
        )
    return tools


def find_capability_by_ref(
    tools: Sequence[CapabilityTool], tool_ref: str
) -> CapabilityTool | None:
    """Find one capability by the opaque ref, never by its display name."""
    return next((tool for tool in tools if tool.tool_ref == tool_ref), None)


def require_valid_capability_arguments(
    schema: dict[str, Any] | None,
    arguments: dict[str, Any],
) -> None:
    """Raise structured validation details before a capability can run."""
    resolved_schema = schema or {"type": "object", "properties": {}}
    issues, schema_error = validate_arguments_against_schema(resolved_schema, arguments)
    if schema_error or issues:
        raise CapabilityArgumentError(issues=issues, schema_error=schema_error)
