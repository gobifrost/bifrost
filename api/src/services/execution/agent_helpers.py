"""Shared helpers for agent execution (used by both chat and autonomous executors)."""
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm.agents import Agent
from src.models.orm.external_mcp import (
    AgentMCPConnection,
    MCPConnection,
    MCPServer,
)
from src.services.llm import ToolDefinition
from src.services.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# Service-token freshness margin for autonomous-run tool inclusion. We
# only filter tools out at planning time when the service token is plainly
# unrecoverable; an expired-but-refreshable token is left in place because
# ``mcp_client.auth_resolution`` will refresh it at dispatch. Matches the
# scheduler's REFRESH_BUFFER_MINUTES so planner and dispatch agree on
# "expired beyond the refresh window."
_AUTONOMOUS_TOKEN_HARD_EXPIRY = timedelta(minutes=5)


# Prefix used for MCP tool names exposed to the LLM. Includes the
# connection UUID so two servers exposing the same tool name (e.g. two
# Microsoft 365 connections) cannot collide in a single agent's toolset.
MCP_TOOL_PREFIX = "mcp__"


def _mcp_tool_qualified_name(connection_id: UUID, tool_name: str) -> str:
    """Build the LLM-visible tool name for an MCP tool.

    Format: ``mcp__<connection_uuid>__<tool_name>``. The executor parses
    this back out at dispatch to look up the connection.
    """
    return f"{MCP_TOOL_PREFIX}{connection_id}__{tool_name}"


def _autonomous_service_token_usable(connection: MCPConnection) -> bool:
    """Quick planner-time check: can this connection serve an autonomous run?

    "Usable" means the connection has a service token row AND the token
    isn't permanently expired. We do NOT attempt a refresh here — that's
    dispatch-time work. A token within the refresh window is still
    considered usable because dispatch can refresh it.
    """
    token = connection.service_oauth_token
    if token is None:
        return False
    if token.expires_at is None:
        return True
    # Treat tokens whose expiry is more than the hard margin in the past
    # as unrecoverable for planning purposes. Tokens still within the
    # refresh window (or only just expired) are left in — dispatch will
    # refresh.
    return token.expires_at > datetime.now(timezone.utc) - _AUTONOMOUS_TOKEN_HARD_EXPIRY


AUTONOMOUS_MODE_SUFFIX = """

---
EXECUTION MODE: You are running autonomously — there is no human in this conversation.
- Your final response MUST be conclusive: a summary, report, action result, or structured output.
- Do NOT ask questions, request clarification, or use phrases like "let me know if you need anything else."
- If you lack information, state what you could not determine and why, then provide the best result possible with available data.
- If a tool call fails, attempt reasonable alternatives before reporting failure."""


def agent_delegation_slug(name: str) -> str:
    """Generate the tool name slug for a delegated agent."""
    return f"delegate_to_{name.lower().replace(' ', '_')}"


def find_delegated_agent(agent: Agent, tool_name: str) -> Agent | None:
    """Match a delegate_to_* tool name to the target agent."""
    for d in (agent.delegated_agents or []):
        if agent_delegation_slug(d.name) == tool_name and d.is_active:
            return d
    return None


def build_agent_system_prompt(
    agent: Agent,
    *,
    execution_context: dict | None = None,
) -> str:
    """Build the system prompt from agent configuration.

    When execution_context has mode="autonomous", appends instructions
    telling the LLM to produce conclusive output (no follow-up questions).
    """
    prompt = agent.system_prompt

    if execution_context and execution_context.get("mode") == "autonomous":
        prompt += AUTONOMOUS_MODE_SUFFIX

    return prompt


async def resolve_agent_tools(
    agent: Agent,
    session: AsyncSession,
    *,
    caller_user_id: UUID | None = None,
) -> tuple[list[ToolDefinition], dict[str, UUID]]:
    """Resolve tool definitions for an agent.

    Args:
        agent: The agent whose tools we're resolving.
        session: Active async DB session for catalog reads.
        caller_user_id: User invoking the agent (chat / claim-bearing
            webhook), or ``None`` for autonomous runs. Controls whether
            MCP tools that require per-user OAuth get included in the
            planner-visible toolset.

    Returns:
        ``(tool_definitions, id_map)``. The id map carries:

        - workflow tool name -> workflow UUID (for the existing dispatch
          path)
        - MCP qualified tool name -> ``MCPConnection.id`` (so the
          executor can load the connection on dispatch)
    """
    tool_registry = ToolRegistry(session)
    tool_definitions: list[ToolDefinition] = []
    tool_workflow_id_map: dict[str, UUID] = {}
    seen_names: dict[str, str] = {}

    # 1. System tools first (they always win conflicts)
    system_tool_ids = list(agent.system_tools or [])

    # Auto-add search_knowledge when agent has knowledge sources
    if agent.knowledge_sources and "search_knowledge" not in system_tool_ids:
        system_tool_ids.append("search_knowledge")

    if system_tool_ids:
        from src.services.mcp_server.server import get_system_tools

        all_system_tools = get_system_tools()
        tool_map = {t["id"]: t for t in all_system_tools}

        for tool_id in system_tool_ids:
            if tool_id in tool_map:
                t = tool_map[tool_id]
                td = ToolDefinition(
                    name=t["id"],
                    description=t["description"],
                    parameters=t["parameters"],
                )
                seen_names[td.name] = f"system tool '{td.name}'"
                tool_definitions.append(td)

    # 2. Workflow tools (sorted by ID for determinism)
    tool_ids = [tool.id for tool in agent.tools]

    if tool_ids:
        workflow_tool_defs = await tool_registry.get_tool_definitions(tool_ids)
        workflow_tool_defs_sorted = sorted(workflow_tool_defs, key=lambda t: str(t.id))

        for td in workflow_tool_defs_sorted:
            if td.name not in seen_names:
                seen_names[td.name] = f"workflow '{td.workflow_name}'"
                tool_workflow_id_map[td.name] = td.id
                tool_definitions.append(
                    ToolDefinition(
                        name=td.name,
                        description=td.description,
                        parameters=td.parameters,
                    )
                )
            else:
                logger.warning(
                    f"Tool name conflict: workflow '{td.workflow_name}' ({td.name}) "
                    f"hidden by {seen_names[td.name]}"
                )

    # 3. Delegation tools (use already-loaded relationship, no extra query)
    if agent.delegated_agents:
        for delegated in agent.delegated_agents:
            if not delegated.is_active:
                continue
            tool_name = agent_delegation_slug(delegated.name)
            if tool_name not in seen_names:
                tool_definitions.append(
                    ToolDefinition(
                        name=tool_name,
                        description=f"Delegate a task to {delegated.name}. {delegated.description or ''}",
                        parameters={
                            "type": "object",
                            "properties": {
                                "task": {
                                    "type": "string",
                                    "description": "The task or question to delegate to this agent",
                                },
                            },
                            "required": ["task"],
                        },
                    )
                )

    # 4. External MCP tools — surfaced from this org's MCPConnections.
    #
    # MCP connections are strictly per-org (organization_id NOT NULL on
    # the connection row), so we filter by the agent's organization_id.
    # An agent with no organization_id (platform-level) gets no MCP
    # tools; per the spec, MCP tools never bind to platform-level
    # agents. Token resolution happens at dispatch — we only filter
    # autonomous runs here when there's no usable service token at all.
    #
    # Per-agent grant filter: only connections explicitly bound to this
    # agent via ``agent_mcp_connections`` are surfaced. Without a grant
    # the agent gets zero MCP tools, regardless of the connection's
    # availability flags. The org filter is belt-and-suspenders — grants
    # should already imply org match, but the join guards against a
    # stale grant from a recreated connection.
    if agent.organization_id is not None:
        connection_rows = await session.execute(
            select(MCPConnection)
            .join(
                AgentMCPConnection,
                AgentMCPConnection.connection_id == MCPConnection.id,
            )
            .where(AgentMCPConnection.agent_id == agent.id)
            .where(MCPConnection.organization_id == agent.organization_id)
            .options(
                selectinload(MCPConnection.tools),
                selectinload(MCPConnection.service_oauth_token),
                selectinload(MCPConnection.server).selectinload(
                    MCPServer.oauth_provider
                ),
            )
        )
        for connection in connection_rows.scalars():
            # Determine if the connection's OAuth flow allows per-user
            # delegation. client_credentials has no per-user mode at all —
            # the only credential is the service token, gated by the two
            # visibility flags. authorization_code can fall back to
            # per-user tokens when chat users have OAuth'd individually.
            provider = (
                connection.server.oauth_provider
                if connection.server is not None
                else None
            )
            flow_type = provider.oauth_flow_type if provider else None
            user_delegation_possible = flow_type != "client_credentials"

            # Autonomous-run gate: connections that can't possibly serve an
            # autonomous call. The MisconfigError path 5 in auth_resolution
            # exists to catch planner bugs, not as a normal-operation outcome.
            if caller_user_id is None:
                if not connection.available_to_autonomous:
                    continue
                if not _autonomous_service_token_usable(connection):
                    continue
            else:
                # Chat-run gate: when no per-user OAuth path is possible
                # (client_credentials flow), the only available token is
                # the service token. Skip the connection if neither it can
                # be used in chat nor would the user be able to OAuth on
                # their own to acquire one.
                if not user_delegation_possible:
                    if not connection.available_in_chat:
                        continue
                    if not _autonomous_service_token_usable(connection):
                        # No service token healthy → can't serve a call.
                        continue

            for catalog_row in connection.tools:
                if not catalog_row.enabled:
                    continue

                qualified_name = _mcp_tool_qualified_name(
                    connection.id, catalog_row.tool_name
                )
                if qualified_name in seen_names:
                    # Should never collide with system/workflow/delegation
                    # tools because of the ``mcp__<uuid>__`` prefix, but
                    # be defensive.
                    logger.warning(
                        "MCP tool name collision on %s; skipping", qualified_name
                    )
                    continue

                schema = catalog_row.tool_schema or {}
                description = schema.get("description") or (
                    f"External MCP tool '{catalog_row.tool_name}' "
                    f"on connection {connection.id}"
                )
                # MCP tools advertise their argument schema under
                # ``inputSchema`` per the spec; some servers use
                # ``input_schema``. Accept either.
                parameters = (
                    schema.get("inputSchema")
                    or schema.get("input_schema")
                    or {"type": "object", "properties": {}}
                )

                seen_names[qualified_name] = (
                    f"MCP tool '{catalog_row.tool_name}' on connection {connection.id}"
                )
                tool_workflow_id_map[qualified_name] = connection.id
                tool_definitions.append(
                    ToolDefinition(
                        name=qualified_name,
                        description=description,
                        parameters=parameters,
                    )
                )

    return tool_definitions, tool_workflow_id_map


def parse_mcp_tool_name(qualified_name: str) -> tuple[UUID, str] | None:
    """Parse an LLM-visible MCP tool name back into ``(connection_id, tool_name)``.

    Returns ``None`` if the name doesn't look like an MCP tool — the
    caller should treat that as "this isn't an MCP tool, route elsewhere"
    rather than as an error. Validates the connection-id segment as a
    UUID so a malformed prefix can't be silently routed.
    """
    if not qualified_name.startswith(MCP_TOOL_PREFIX):
        return None
    payload = qualified_name[len(MCP_TOOL_PREFIX):]
    parts = payload.split("__", 1)
    if len(parts) != 2:
        return None
    raw_id, tool_name = parts
    try:
        connection_id = UUID(raw_id)
    except (ValueError, TypeError):
        return None
    if not tool_name:
        return None
    return connection_id, tool_name
