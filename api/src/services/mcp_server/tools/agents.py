"""
Agent MCP Tools

Tools for listing, creating, updating, and managing AI agents.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest
from src.services.mcp_server.tools.db import get_tool_db

# MCPContext is imported where needed to avoid circular imports

logger = logging.getLogger(__name__)


# ==================== SCHEMA TOOL ====================


async def get_agent_schema(context: Any) -> ToolResult:  # noqa: ARG001
    """Get agent schema documentation generated from Pydantic models."""
    from src.models.contracts.agents import AgentCreate, AgentUpdate
    from src.services.mcp_server.schema_utils import models_to_markdown

    # Generate markdown from Pydantic models
    model_docs = models_to_markdown([
        (AgentCreate, "AgentCreate (for creating agents)"),
        (AgentUpdate, "AgentUpdate (for updating agents)"),
    ], "Agent Schema Documentation")

    # Add channel enum documentation
    channels_doc = """
## Available Channels

| Channel | Description |
|---------|-------------|
| chat | Web chat interface |
| voice | Voice/phone integration |
| teams | Microsoft Teams bot |
| slack | Slack bot integration |

## Usage Notes

- **tool_ids**: UUIDs of workflows to assign as callable tools. Use `list_workflows` to find available tools.
- **delegated_agent_ids**: UUIDs of agents this agent can delegate to for specialized tasks.
- **knowledge_sources**: Knowledge namespaces for RAG search.
- **system_tools**: Built-in MCP tool names to enable (e.g., "list_tables", "query_table").
- **scope**: Use "global" for visibility to all orgs, or "organization" (default).

## MCP Tools for Agents

- `list_agents` - List all accessible agents
- `get_agent` - Get agent details by ID or name
- `create_agent` - Create a new agent
- `update_agent` - Update agent properties
- `delete_agent` - Permanently delete an agent
"""

    schema_doc = model_docs + channels_doc
    return success_result("Agent schema documentation", {"schema": schema_doc})


# ==================== LIST/GET TOOLS ====================


async def list_agents(context: Any) -> ToolResult:
    """List all agents."""
    from src.repositories.agents import AgentRepository

    logger.info("MCP list_agents called")

    try:
        async with get_tool_db(context) as db:
            if context.org_id:
                org_id = UUID(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id
            else:
                org_id = None

            # Get user_id from context if available
            user_id = None
            if hasattr(context, "user_id") and context.user_id:
                user_id = UUID(str(context.user_id)) if isinstance(context.user_id, str) else context.user_id

            repo = AgentRepository(
                session=db,
                org_id=org_id,
                user_id=user_id,
                is_superuser=False,
                is_external=context.is_external,
            )
            agents = await repo.list_agents(active_only=True)

            agents_data = [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "description": agent.description,
                    "channels": agent.channels,
                    "is_active": agent.is_active,
                    "llm_profile_id": str(agent.llm_profile_id) if agent.llm_profile_id else None,
                }
                for agent in agents
            ]

            display_text = f"Found {len(agents_data)} agent(s)"
            return success_result(display_text, {"agents": agents_data, "count": len(agents_data)})

    except Exception as e:
        logger.exception(f"Error listing agents via MCP: {e}")
        return error_result(f"Error listing agents: {str(e)}")


async def get_agent(
    context: Any,
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> ToolResult:
    """Get detailed information about a specific agent.

    Args:
        context: MCP context with user permissions
        agent_id: Agent UUID (preferred)
        agent_name: Agent name (alternative to ID)

    Returns:
        ToolResult with agent details
    """
    from sqlalchemy import select

    from shared.scope_resolver import has_scope_bypass
    from src.models.orm import Agent
    from src.repositories.agents import AgentRepository

    logger.info(f"MCP get_agent called: agent_id={agent_id}, agent_name={agent_name}")

    if not agent_id and not agent_name:
        return error_result("Either agent_id or agent_name is required")

    try:
        async with get_tool_db(context) as db:
            org_id = (
                UUID(str(context.org_id))
                if context.org_id and isinstance(context.org_id, str)
                else context.org_id
            )
            user_id = (
                UUID(str(context.user_id))
                if getattr(context, "user_id", None) and isinstance(context.user_id, str)
                else getattr(context, "user_id", None)
            )
            repo = AgentRepository(
                session=db,
                org_id=org_id,
                user_id=user_id,
                is_superuser=has_scope_bypass(
                    is_platform_admin=getattr(context, "is_platform_admin", False),
                    is_provider_org=getattr(context, "is_provider_org", False),
                ),
                is_external=getattr(context, "is_external", False),
            )

            if agent_id:
                try:
                    uuid_id = UUID(agent_id)
                except ValueError:
                    return error_result(f"'{agent_id}' is not a valid UUID")
                # Same access-check REST uses for GET /api/agents/{id}: org
                # cascade + role-based/private access-level enforcement.
                agent = await repo.get_agent_with_access_check(uuid_id)
            else:
                # Name-based lookup: resolve the candidate id via the same
                # org-cascade priority as before, then re-verify it through
                # the identical access check so a name lookup can't read a
                # PRIVATE agent or a role-gated agent the caller lacks.
                name_query = select(Agent.id).where(Agent.name == agent_name)
                if repo.org_id is not None:
                    # Prioritize the org-specific row over a same-named
                    # global row.
                    name_query = name_query.order_by(
                        Agent.organization_id.desc().nulls_last()
                    )
                elif not repo.is_superuser:
                    # No org context and not a bypass caller: only a global
                    # row is even a candidate (legacy "no org -> global
                    # only" cascade) — an org-scoped same-named row must
                    # never resolve here.
                    name_query = name_query.where(Agent.organization_id.is_(None))
                candidate_id = (await db.execute(name_query.limit(1))).scalar_one_or_none()
                agent = (
                    await repo.get_agent_with_access_check(candidate_id)
                    if candidate_id is not None
                    else None
                )

            if not agent:
                identifier = agent_id or agent_name
                return error_result(f"Agent '{identifier}' not found. Use list_agents to see available agents.")

            agent_data = {
                "id": str(agent.id),
                "name": agent.name,
                "description": agent.description,
                "system_prompt": agent.system_prompt,
                "channels": agent.channels,
                "access_level": agent.access_level.value if agent.access_level else "role_based",
                "organization_id": str(agent.organization_id) if agent.organization_id else None,
                "is_active": agent.is_active,
                "created_by": agent.created_by,
                "created_at": agent.created_at.isoformat() if agent.created_at else None,
                "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
                "tool_ids": [str(t.id) for t in agent.tools] if agent.tools else [],
                "delegated_agent_ids": [str(a.id) for a in agent.delegated_agents] if agent.delegated_agents else [],
                "role_ids": [str(r.id) for r in agent.roles] if agent.roles else [],
                "knowledge_sources": agent.knowledge_sources or [],
                "system_tools": agent.system_tools or [],
                "llm_profile_id": str(agent.llm_profile_id) if agent.llm_profile_id else None,
                "llm_max_tokens": agent.llm_max_tokens,
            }

            display_text = f"Agent: {agent.name}"
            return success_result(display_text, agent_data)

    except Exception as e:
        logger.exception(f"Error getting agent via MCP: {e}")
        return error_result(f"Error getting agent: {str(e)}")


async def create_agent(
    context: Any,
    name: str,
    system_prompt: str,
    description: str | None = None,
    channels: list[str] | None = None,
    tool_ids: list[str] | None = None,
    delegated_agent_ids: list[str] | None = None,
    knowledge_sources: list[str] | None = None,
    system_tools: list[str] | None = None,
    scope: str = "organization",
    organization_id: str | None = None,
    llm_profile_id: str | None = None,
    llm_max_tokens: int | None = None,
) -> ToolResult:
    """Create a new agent.

    Args:
        context: MCP context with user permissions
        name: Agent name (1-255 chars)
        system_prompt: System prompt that defines the agent's behavior
        description: Optional description of what the agent does
        channels: Communication channels (default: ['chat'])
        tool_ids: List of workflow IDs to assign as tools
        delegated_agent_ids: List of agent IDs this agent can delegate to
        knowledge_sources: List of knowledge namespaces this agent can search
        system_tools: List of system tool names enabled for this agent
        scope: 'global' (visible to all orgs) or 'organization' (default)
        organization_id: Override context.org_id when scope='organization'
        llm_profile_id: Optional model profile UUID for this agent

    Returns:
        ToolResult with created agent details
    """
    logger.info(f"MCP create_agent called: name={name}, scope={scope}")

    if not name:
        return error_result("name is required")
    if not system_prompt:
        return error_result("system_prompt is required")
    if len(name) > 255:
        return error_result("name must be 255 characters or less")
    if len(system_prompt) > 50000:
        return error_result("system_prompt must be 50000 characters or less")
    if scope not in ("global", "organization"):
        return error_result("scope must be 'global' or 'organization'")

    valid_channels = {"chat", "voice", "teams", "slack"}
    if channels:
        invalid_channels = set(channels) - valid_channels
        if invalid_channels:
            return error_result(f"Invalid channels: {list(invalid_channels)}. Valid options: {list(valid_channels)}")
    else:
        channels = ["chat"]

    if llm_profile_id:
        try:
            UUID(llm_profile_id)
        except ValueError:
            return error_result(f"llm_profile_id '{llm_profile_id}' is not a valid UUID")

    # organization_id follows REST's own resolution: global scope means
    # explicit null; organization scope defaults to the caller's org.
    effective_org_id: str | None = None
    if scope == "organization":
        if organization_id:
            effective_org_id = organization_id
        elif context.org_id:
            effective_org_id = str(context.org_id)
        else:
            return error_result("organization_id is required when scope='organization' and no context org_id is set")

    # Thin wrapper over POST /api/agents: REST enforces the non-admin
    # rules (forced PRIVATE access_level, forced own org, admin-only
    # fields stripped to empty, tool_ids validated against the caller's
    # own accessible tools) — MCP does not re-implement that logic.
    body = {
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "channels": channels,
        "access_level": "role_based",
        "organization_id": effective_org_id,
        "tool_ids": tool_ids or [],
        "delegated_agent_ids": delegated_agent_ids or [],
        "knowledge_sources": knowledge_sources or [],
        "system_tools": system_tools or [],
        "llm_profile_id": llm_profile_id,
        "llm_max_tokens": llm_max_tokens,
    }

    status_code, resp = await call_rest(context, "POST", "/api/agents", json_body=body)
    if status_code not in (200, 201):
        return error_result(f"create_agent failed: HTTP {status_code}", {"body": resp})

    agent_data = resp if isinstance(resp, dict) else {"body": resp}
    display_text = f"Created agent: {agent_data.get('name', name)}"
    return success_result(display_text, {"success": True, **agent_data})


async def update_agent(
    context: Any,
    agent_id: str,
    name: str | None = None,
    description: str | None = None,
    system_prompt: str | None = None,
    channels: list[str] | None = None,
    is_active: bool | None = None,
    tool_ids: list[str] | None = None,
    delegated_agent_ids: list[str] | None = None,
    knowledge_sources: list[str] | None = None,
    system_tools: list[str] | None = None,
    llm_profile_id: str | None = None,
    llm_max_tokens: int | None = None,
) -> ToolResult:
    """Update an existing agent.

    Args:
        context: MCP context with user permissions
        agent_id: Agent UUID (required)
        name: New agent name
        description: New description
        system_prompt: New system prompt
        channels: New communication channels
        is_active: Enable/disable the agent
        tool_ids: New list of workflow IDs (replaces existing)
        delegated_agent_ids: New list of delegated agent IDs (replaces existing)
        knowledge_sources: New list of knowledge namespaces
        system_tools: New list of system tool names
        llm_profile_id: New model profile UUID

    Returns:
        ToolResult with update confirmation
    """
    from sqlalchemy import delete, select
    from sqlalchemy.orm import selectinload

    from shared.scope_resolver import has_scope_bypass
    from src.models.enums import AgentAccessLevel
    from src.models.orm import Agent, AgentDelegation, AgentTool, AIModelProfile, Workflow

    logger.info(f"MCP update_agent called: agent_id={agent_id}")

    if not agent_id:
        return error_result("agent_id is required")

    # Validate agent_id is a valid UUID
    try:
        uuid_id = UUID(agent_id)
    except ValueError:
        return error_result(f"'{agent_id}' is not a valid UUID")

    # Validate channels if provided
    valid_channels = {"chat", "voice", "teams", "slack"}
    if channels:
        invalid_channels = set(channels) - valid_channels
        if invalid_channels:
            return error_result(f"Invalid channels: {list(invalid_channels)}. Valid options: {list(valid_channels)}")

    profile_uuid: UUID | None = None
    if llm_profile_id:
        try:
            profile_uuid = UUID(llm_profile_id)
        except ValueError:
            return error_result(f"llm_profile_id '{llm_profile_id}' is not a valid UUID")

    try:
        async with get_tool_db(context) as db:
            # Get existing agent
            result = await db.execute(
                select(Agent)
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                    selectinload(Agent.roles),
                )
                .where(Agent.id == uuid_id)
            )
            agent = result.scalar_one_or_none()

            if not agent:
                return error_result(f"Agent '{agent_id}' not found. Use list_agents to see available agents.")

            # Solution-managed agents are read-only (criterion 6). Refuse BEFORE
            # any mutation: this tool issues Core bulk deletes (AgentTool /
            # AgentDelegation) that bypass the ORM-flush backstop, and the agent
            # executor commits even after an error_result — so a late guard would
            # leave the bulk delete persisted (Codex #13).
            from src.services.solutions.guard import (
                SOLUTION_MANAGED_MESSAGE,
                is_solution_managed,
            )

            if is_solution_managed(agent):
                return error_result(SOLUTION_MANAGED_MESSAGE)

            # Check access for non-bypass callers. Mirrors the REST rule in
            # routers/agents.py::update_agent (owner_user_id == caller AND
            # access_level == PRIVATE), rather than an organization_id
            # comparison — a non-bypass caller may never edit a role-based
            # or global agent, only their own private one.
            is_bypass = has_scope_bypass(
                is_platform_admin=bool(context.is_platform_admin),
                is_provider_org=bool(getattr(context, "is_provider_org", False)),
            )
            if not is_bypass:
                # Budget fields gate: only bypass callers can set per-agent
                # budgets. Checked before the ownership check so the
                # response is the same whether the caller owns the agent or
                # not (no information leak about ownership).
                if llm_max_tokens is not None:
                    return error_result(
                        "Budget fields (llm_max_tokens) can only be set by platform administrators"
                    )

                caller_id = getattr(context, "user_id", None)
                if (
                    caller_id is None
                    or agent.owner_user_id is None
                    or str(agent.owner_user_id) != str(caller_id)
                    or agent.access_level != AgentAccessLevel.PRIVATE
                ):
                    return error_result("You can only edit your own private agents")

                # Non-owners-with-access-but-not-bypass cannot manage these
                # privileged fields via this tool; drop them silently rather
                # than error, matching REST's behavior for the same fields.
                system_tools = None
                knowledge_sources = None
                delegated_agent_ids = None

            updates_made = []

            # Apply updates
            if name is not None:
                if len(name) > 255:
                    return error_result("name must be 255 characters or less")
                agent.name = name
                updates_made.append("name")

            if description is not None:
                agent.description = description
                updates_made.append("description")

            if system_prompt is not None:
                if len(system_prompt) > 50000:
                    return error_result("system_prompt must be 50000 characters or less")
                agent.system_prompt = system_prompt
                updates_made.append("system_prompt")

            if channels is not None:
                agent.channels = channels
                updates_made.append("channels")

            if is_active is not None:
                agent.is_active = is_active
                updates_made.append("is_active")

            if knowledge_sources is not None:
                agent.knowledge_sources = knowledge_sources
                updates_made.append("knowledge_sources")

            if system_tools is not None:
                agent.system_tools = system_tools
                updates_made.append("system_tools")

            if llm_profile_id is not None:
                if profile_uuid is None:
                    agent.llm_profile_id = None
                else:
                    profile_exists = await db.scalar(
                        select(AIModelProfile.id).where(AIModelProfile.id == profile_uuid)
                    )
                    if profile_exists is None:
                        return error_result(f"llm_profile_id '{llm_profile_id}' does not reference an existing model profile")
                    agent.llm_profile_id = profile_uuid
                updates_made.append("llm_profile_id")

            if llm_max_tokens is not None:
                agent.llm_max_tokens = llm_max_tokens if llm_max_tokens > 0 else None
                updates_made.append("llm_max_tokens")

            agent.updated_at = datetime.now(timezone.utc)

            # Update tool relationships if provided
            tools: list[Workflow] = []
            if tool_ids is not None:
                await db.execute(
                    delete(AgentTool).where(AgentTool.agent_id == uuid_id)
                )
                for tool_id in tool_ids:
                    try:
                        workflow_uuid = UUID(tool_id)
                        result = await db.execute(
                            select(Workflow)
                            .where(Workflow.id == workflow_uuid)
                            .where(Workflow.type == "tool")
                            .where(Workflow.is_active.is_(True))
                        )
                        workflow = result.scalar_one_or_none()
                        if workflow:
                            tools.append(workflow)
                            db.add(AgentTool(agent_id=uuid_id, workflow_id=workflow.id))
                    except ValueError:
                        logger.warning(f"Invalid tool ID: {tool_id}")
                updates_made.append("tool_ids")

            # Update delegation relationships if provided
            delegated_agents: list[Agent] = []
            if delegated_agent_ids is not None:
                await db.execute(
                    delete(AgentDelegation).where(AgentDelegation.parent_agent_id == uuid_id)
                )
                for delegate_id in delegated_agent_ids:
                    try:
                        delegate_uuid = UUID(delegate_id)
                        if delegate_uuid == uuid_id:
                            logger.warning("Agent cannot delegate to itself, skipping")
                            continue
                        result = await db.execute(
                            select(Agent)
                            .where(Agent.id == delegate_uuid)
                            .where(Agent.is_active.is_(True))
                        )
                        delegate = result.scalar_one_or_none()
                        if delegate:
                            delegated_agents.append(delegate)
                            db.add(AgentDelegation(
                                parent_agent_id=uuid_id,
                                child_agent_id=delegate.id,
                            ))
                    except ValueError:
                        logger.warning(f"Invalid delegate agent ID: {delegate_id}")
                updates_made.append("delegated_agent_ids")

            if not updates_made:
                return error_result("No updates provided. Specify at least one field to update.")

            await db.flush()

            # Reload with relationships
            result = await db.execute(
                select(Agent)
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                    selectinload(Agent.roles),
                )
                .where(Agent.id == uuid_id)
            )
            agent = result.scalar_one()

            logger.info(f"Updated agent {agent.id}: {', '.join(updates_made)}")

            display_text = f"Updated agent: {agent.name} ({', '.join(updates_made)})"
            return success_result(display_text, {
                "success": True,
                "id": str(agent.id),
                "name": agent.name,
                "updates": updates_made,
            })

    except Exception as e:
        logger.exception(f"Error updating agent via MCP: {e}")
        return error_result(f"Error updating agent: {str(e)}")


async def delete_agent(
    context: Any,
    agent_id: str,
) -> ToolResult:
    """Permanently delete an agent through the canonical REST endpoint.

    Args:
        context: MCP context with user permissions
        agent_id: Agent UUID

    Returns:
        ToolResult with deletion confirmation
    """
    logger.info(f"MCP delete_agent called: agent_id={agent_id}")

    if not agent_id:
        return error_result("agent_id is required")

    try:
        uuid_id = UUID(agent_id)
    except ValueError:
        return error_result(f"'{agent_id}' is not a valid UUID")

    status_code, body = await call_rest(
        context,
        "DELETE",
        f"/api/agents/{uuid_id}",
    )
    if status_code != 204:
        return error_result(
            f"delete_agent failed: HTTP {status_code}",
            {"body": body},
        )

    return success_result(
        f"Deleted agent {uuid_id}",
        {"deleted": str(uuid_id)},
    )


# Tool metadata for registration
TOOLS = [
("list_agents", "List Agents", "List all AI agents accessible to the current user."),
    ("get_agent", "Get Agent", "Get detailed information about a specific agent including assigned tools and delegation targets."),
    ("create_agent", "Create Agent", "Create a new AI agent with system prompt and configuration."),
    ("update_agent", "Update Agent", "Update an existing agent's properties."),
    ("delete_agent", "Delete Agent", "Permanently delete an agent."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all agents tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
"list_agents": list_agents,
        "get_agent": get_agent,
        "create_agent": create_agent,
        "update_agent": update_agent,
        "delete_agent": delete_agent,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
