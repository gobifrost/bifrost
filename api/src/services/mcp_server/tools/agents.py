"""
Agent MCP Tools

Tools for listing, creating, updating, and managing AI agents.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from uuid import uuid4

from fastmcp.tools import ToolResult

from src.core.system_agents import PRIVILEGED_AGENT_MANAGEMENT_TOOLS
from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools.db import get_tool_db

# MCPContext is imported where needed to avoid circular imports

logger = logging.getLogger(__name__)


def _ensure_can_manage_agent_privileges(
    context: Any,
    *,
    scope: str | None = None,
    system_tools: list[str] | None = None,
    delegated_agent_ids: list[str] | None = None,
    knowledge_sources: list[str] | None = None,
) -> ToolResult | None:
    """Block non-admin MCP callers from granting sensitive agent capabilities."""
    if getattr(context, "is_platform_admin", False):
        return None

    if scope == "global":
        return error_result("Only platform admins can create global agents.")

    if system_tools:
        privileged = sorted(set(system_tools) & PRIVILEGED_AGENT_MANAGEMENT_TOOLS)
        if privileged:
            return error_result(
                "Only platform admins can grant privileged agent management tools: "
                + ", ".join(privileged)
            )

    if delegated_agent_ids:
        return error_result("Only platform admins can configure agent delegation via MCP.")

    if knowledge_sources:
        return error_result("Only platform admins can configure agent knowledge sources via MCP.")

    return None


def _reference_in_agent_scope(reference_org_id: Any, agent_org_id: UUID | None) -> bool:
    """Return whether a referenced tool/agent may be linked to this agent."""
    return reference_org_id is None or reference_org_id == agent_org_id


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
- `delete_agent` - Soft-delete an agent (deactivate)
"""

    schema_doc = model_docs + channels_doc
    return success_result("Agent schema documentation", {"schema": schema_doc})


# ==================== LIST/GET TOOLS ====================


async def list_agents(context: Any) -> ToolResult:
    """List all agents."""
    from src.core.org_filter import OrgFilterType
    from src.repositories.agents import AgentRepository

    logger.info("MCP list_agents called")

    try:
        async with get_tool_db(context) as db:
            # Determine org_id and admin status based on context
            is_admin = context.is_platform_admin
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
                is_superuser=is_admin,
            )

            if is_admin:
                # Platform admins see all agents using list_all_in_scope
                agents = await repo.list_all_in_scope(OrgFilterType.ALL, active_only=True)
            else:
                # Regular users use list_agents with built-in cascade + role-based access
                agents = await repo.list_agents(active_only=True)

            agents_data = [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "description": agent.description,
                    "channels": agent.channels,
                    "is_active": agent.is_active,
                    "llm_model": agent.llm_model,
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
    from sqlalchemy import or_, select
    from sqlalchemy.orm import selectinload

    from src.models.orm import Agent

    logger.info(f"MCP get_agent called: agent_id={agent_id}, agent_name={agent_name}")

    if not agent_id and not agent_name:
        return error_result("Either agent_id or agent_name is required")

    try:
        async with get_tool_db(context) as db:
            # Build query
            query = select(Agent).options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
                selectinload(Agent.roles),
            )

            if agent_id:
                # ID-based lookup: IDs are unique, so cascade filter is safe
                try:
                    uuid_id = UUID(agent_id)
                except ValueError:
                    return error_result(f"'{agent_id}' is not a valid UUID")
                query = query.where(Agent.id == uuid_id)
                # Apply org scoping for non-admins (cascade filter for ID lookups)
                if not context.is_platform_admin and context.org_id:
                    org_uuid = UUID(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id
                    query = query.where(
                        or_(
                            Agent.organization_id == org_uuid,
                            Agent.organization_id.is_(None)  # Global agents
                        )
                    )
            else:
                # Name-based lookup: use prioritized lookup (org-specific > global)
                query = query.where(Agent.name == agent_name)
                if not context.is_platform_admin and context.org_id:
                    org_uuid = UUID(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id
                    query = query.where(
                        or_(
                            Agent.organization_id == org_uuid,
                            Agent.organization_id.is_(None)  # Global agents
                        )
                    )
                    # Prioritize org-specific over global (nulls come last)
                    query = query.order_by(Agent.organization_id.desc().nulls_last()).limit(1)
                elif not context.is_platform_admin:
                    # No org context - only global agents
                    query = query.where(Agent.organization_id.is_(None))

            result = await db.execute(query)
            agent = result.scalar_one_or_none()

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
                "llm_model": agent.llm_model,
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
    llm_model: str | None = None,
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

    Returns:
        ToolResult with created agent details
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.models.enums import AgentAccessLevel
    from src.models.orm import Agent, AgentDelegation, AgentTool, Workflow

    logger.info(f"MCP create_agent called: name={name}, scope={scope}")

    # Validate inputs
    if not name:
        return error_result("name is required")
    if not system_prompt:
        return error_result("system_prompt is required")
    if len(name) > 255:
        return error_result("name must be 255 characters or less")
    if len(system_prompt) > 50000:
        return error_result("system_prompt must be 50000 characters or less")

    # Validate scope parameter
    if scope not in ("global", "organization"):
        return error_result("scope must be 'global' or 'organization'")

    privilege_error = _ensure_can_manage_agent_privileges(
        context,
        scope=scope,
        system_tools=system_tools,
        delegated_agent_ids=delegated_agent_ids,
        knowledge_sources=knowledge_sources,
    )
    if privilege_error is not None:
        return privilege_error
    if (
        scope == "organization"
        and not context.is_platform_admin
        and organization_id is not None
        and (context.org_id is None or str(organization_id) != str(context.org_id))
    ):
        return error_result("You don't have permission to create agents in another organization.")

    # Validate channels if provided
    valid_channels = {"chat", "voice", "teams", "slack"}
    if channels:
        invalid_channels = set(channels) - valid_channels
        if invalid_channels:
            return error_result(f"Invalid channels: {list(invalid_channels)}. Valid options: {list(valid_channels)}")
    else:
        channels = ["chat"]

    # Determine effective organization_id based on scope
    effective_org_id: UUID | None = None
    if scope == "global":
        # Global resources have no organization_id
        effective_org_id = None
    else:
        # Organization scope: use provided organization_id or fall back to context.org_id
        if organization_id:
            try:
                effective_org_id = UUID(organization_id)
            except ValueError:
                return error_result(f"organization_id '{organization_id}' is not a valid UUID")
        elif context.org_id:
            effective_org_id = UUID(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id
        else:
            return error_result("organization_id is required when scope='organization' and no context org_id is set")

    try:
        async with get_tool_db(context) as db:
            agent_id = uuid4()
            now = datetime.now(timezone.utc)

            # Create the agent
            agent = Agent(
                id=agent_id,
                name=name,
                description=description,
                system_prompt=system_prompt,
                channels=channels,
                access_level=AgentAccessLevel.ROLE_BASED,
                organization_id=effective_org_id,
                is_active=True,
                knowledge_sources=knowledge_sources or [],
                system_tools=system_tools or [],
                llm_model=llm_model,
                llm_max_tokens=llm_max_tokens,
                created_by=context.user_email,
                created_at=now,
                updated_at=now,
            )
            db.add(agent)

            # Add tool relationships
            tools: list[Workflow] = []
            if tool_ids:
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
                            if not _reference_in_agent_scope(workflow.organization_id, effective_org_id):
                                return error_result(
                                    f"Tool workflow '{tool_id}' belongs to a different organization."
                            )
                            tools.append(workflow)
                        else:
                            logger.warning(f"Tool workflow not found or inactive: {tool_id}")
                    except ValueError:
                        logger.warning(f"Invalid tool ID: {tool_id}")

            # Add delegation relationships
            delegated_agents: list[Agent] = []
            if delegated_agent_ids:
                for delegate_id in delegated_agent_ids:
                    try:
                        delegate_uuid = UUID(delegate_id)
                        if delegate_uuid == agent_id:
                            logger.warning("Agent cannot delegate to itself, skipping")
                            continue
                        result = await db.execute(
                            select(Agent)
                            .where(Agent.id == delegate_uuid)
                            .where(Agent.is_active.is_(True))
                        )
                        delegate = result.scalar_one_or_none()
                        if delegate:
                            if not _reference_in_agent_scope(delegate.organization_id, effective_org_id):
                                return error_result(
                                    f"Delegated agent '{delegate_id}' belongs to a different organization."
                            )
                            delegated_agents.append(delegate)
                        else:
                            logger.warning(f"Delegate agent not found or inactive: {delegate_id}")
                    except ValueError:
                        logger.warning(f"Invalid delegate agent ID: {delegate_id}")

            for workflow in tools:
                db.add(AgentTool(agent_id=agent_id, workflow_id=workflow.id))
            for delegate in delegated_agents:
                db.add(AgentDelegation(
                    parent_agent_id=agent_id,
                    child_agent_id=delegate.id,
                ))

            await db.flush()

            # Reload with relationships
            result = await db.execute(
                select(Agent)
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                    selectinload(Agent.roles),
                )
                .where(Agent.id == agent_id)
            )
            agent = result.scalar_one()

            logger.info(f"Created agent {agent.id}: {agent.name}")

            display_text = f"Created agent: {agent.name}"
            return success_result(display_text, {
                "success": True,
                "id": str(agent.id),
                "name": agent.name,
                "description": agent.description,
                "channels": agent.channels,
                "tool_count": len(tools),
                "delegated_agent_count": len(delegated_agents),
            })

    except Exception as e:
        logger.exception(f"Error creating agent via MCP: {e}")
        return error_result(f"Error creating agent: {str(e)}")


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
    llm_model: str | None = None,
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

    Returns:
        ToolResult with update confirmation
    """
    from sqlalchemy import delete, select
    from sqlalchemy.orm import selectinload

    from src.models.orm import Agent, AgentDelegation, AgentTool, Workflow

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

    privilege_error = _ensure_can_manage_agent_privileges(
        context,
        system_tools=system_tools,
        delegated_agent_ids=delegated_agent_ids,
        knowledge_sources=knowledge_sources,
    )
    if privilege_error is not None:
        return privilege_error
    if not context.is_platform_admin and context.org_id is None:
        return error_result("Organization context is required to update agents.")

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

            # Check access for non-admins
            if not context.is_platform_admin:
                if agent.organization_id:
                    if context.org_id and str(agent.organization_id) != str(context.org_id):
                        return error_result("You don't have permission to update this agent.")
                # Global agents can only be updated by admins
                if agent.organization_id is None:
                    return error_result("Only platform admins can update global agents.")

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

            if llm_model is not None:
                agent.llm_model = llm_model if llm_model else None
                updates_made.append("llm_model")

            if llm_max_tokens is not None:
                agent.llm_max_tokens = llm_max_tokens if llm_max_tokens > 0 else None
                updates_made.append("llm_max_tokens")

            agent.updated_at = datetime.now(timezone.utc)

            # Update tool relationships if provided
            tools: list[Workflow] = []
            if tool_ids is not None:
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
                            if not _reference_in_agent_scope(workflow.organization_id, agent.organization_id):
                                return error_result(
                                    f"Tool workflow '{tool_id}' belongs to a different organization."
                            )
                            tools.append(workflow)
                    except ValueError:
                        logger.warning(f"Invalid tool ID: {tool_id}")
                await db.execute(
                    delete(AgentTool).where(AgentTool.agent_id == uuid_id)
                )
                for workflow in tools:
                    db.add(AgentTool(agent_id=uuid_id, workflow_id=workflow.id))
                updates_made.append("tool_ids")

            # Update delegation relationships if provided
            delegated_agents: list[Agent] = []
            if delegated_agent_ids is not None:
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
                            if not _reference_in_agent_scope(delegate.organization_id, agent.organization_id):
                                return error_result(
                                    f"Delegated agent '{delegate_id}' belongs to a different organization."
                            )
                            delegated_agents.append(delegate)
                    except ValueError:
                        logger.warning(f"Invalid delegate agent ID: {delegate_id}")
                await db.execute(
                    delete(AgentDelegation).where(AgentDelegation.parent_agent_id == uuid_id)
                )
                for delegate in delegated_agents:
                    db.add(AgentDelegation(
                        parent_agent_id=uuid_id,
                        child_agent_id=delegate.id,
                    ))
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
    """Delete an agent (soft delete).

    Args:
        context: MCP context with user permissions
        agent_id: Agent UUID

    Returns:
        ToolResult with deletion confirmation
    """
    from sqlalchemy import select

    from src.models.orm import Agent

    logger.info(f"MCP delete_agent called: agent_id={agent_id}")

    if not agent_id:
        return error_result("agent_id is required")

    # Validate agent_id is a valid UUID
    try:
        uuid_id = UUID(agent_id)
    except ValueError:
        return error_result(f"'{agent_id}' is not a valid UUID")

    try:
        async with get_tool_db(context) as db:
            result = await db.execute(
                select(Agent).where(Agent.id == uuid_id)
            )
            agent = result.scalar_one_or_none()

            if not agent:
                return error_result(f"Agent '{agent_id}' not found. Use list_agents to see available agents.")

            # Check access for non-admins
            if not context.is_platform_admin:
                if agent.organization_id:
                    if context.org_id and str(agent.organization_id) != str(context.org_id):
                        return error_result("You don't have permission to delete this agent.")
                # Global agents can only be deleted by admins
                if agent.organization_id is None:
                    return error_result("Only platform admins can delete global agents.")

            # Soft delete
            agent.is_active = False
            agent.updated_at = datetime.now(timezone.utc)
            await db.flush()

            logger.info(f"Deleted (soft) agent {agent.id}: {agent.name}")

            display_text = f"Deleted agent: {agent.name}"
            return success_result(display_text, {
                "success": True,
                "id": str(agent.id),
                "name": agent.name,
                "message": f"Agent '{agent.name}' has been deactivated.",
            })

    except Exception as e:
        logger.exception(f"Error deleting agent via MCP: {e}")
        return error_result(f"Error deleting agent: {str(e)}")


# Tool metadata for registration
TOOLS = [
("list_agents", "List Agents", "List all AI agents accessible to the current user."),
    ("get_agent", "Get Agent", "Get detailed information about a specific agent including assigned tools and delegation targets."),
    ("create_agent", "Create Agent", "Create a new AI agent with system prompt and configuration."),
    ("update_agent", "Update Agent", "Update an existing agent's properties."),
    ("delete_agent", "Delete Agent", "Delete an agent (soft delete - sets is_active to false)."),
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
