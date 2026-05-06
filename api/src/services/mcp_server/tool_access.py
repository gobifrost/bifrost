"""
MCP Tool Access Service

Computes which MCP tools a user can access based on their agent access permissions.
Tools are sourced from agents the user has access to via role assignments.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.contracts.agents import ToolInfo
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.routers.tools import get_system_tools
from src.services.mcp_server.config_service import MCPConfig, MCPConfigService

logger = logging.getLogger(__name__)


@dataclass
class MCPToolAccessResult:
    """Result of computing accessible MCP tools."""

    tools: list[ToolInfo]
    accessible_agent_ids: list[UUID]
    accessible_namespaces: list[str]  # Knowledge namespaces from accessible agents


@dataclass
class AgentScopedToolResult:
    """Result of computing tools for a specific agent."""

    tools: list[ToolInfo]
    agent_id: UUID
    agent_name: str
    system_prompt: str
    accessible_namespaces: list[str]


class MCPToolAccessService:
    """
    Service for computing which MCP tools a user can access.

    Access flow:
    1. Determine which agents the user can access via their roles
    2. Collect system_tools and workflow tools from accessible agents
    3. Apply global MCP config allowlist/blocklist
    4. Return the final tool list

    Tool sources per agent:
    - System tools: agent.system_tools array (e.g., ["execute_workflow", "list_workflows"])
    - Workflow tools: agent.tools relationship (workflows assigned via agent_tools table)
    """

    # Map system tool IDs to their metadata from get_system_tools()
    _SYSTEM_TOOL_MAP: dict[str, ToolInfo] = {tool.id: tool for tool in get_system_tools()}

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_accessible_tools(
        self,
        user_roles: list[str],
        is_superuser: bool,
        user_id: UUID | str | None = None,
        org_id: UUID | str | None = None,
    ) -> MCPToolAccessResult:
        """
        Get all MCP tools accessible to the user.

        Per-workflow visibility is enforced through ``WorkflowRepository`` —
        the same gate the executor uses. This means tools/list and tools/call
        cannot drift: if the user can list a workflow, they can execute it,
        and vice versa.

        Args:
            user_roles: List of role names the user has (from JWT claims)
            is_superuser: Whether user is platform admin
            user_id: User UUID for per-workflow role check (from JWT claims).
                Required for non-superusers — without it, role-based workflows
                fall back to "deny" because role membership cannot be checked.
            org_id: User's organization UUID for per-workflow org scope check
                (from JWT claims). Required for non-superusers — without it,
                org-scoped workflows fall back to "deny" because the in-scope
                check cannot evaluate.

        Returns:
            MCPToolAccessResult with tools and accessible agent IDs
        """
        # Step 1: Get accessible agents
        accessible_agents = await self._get_accessible_agents(
            user_roles=user_roles,
            is_superuser=is_superuser,
        )

        # Step 2: Collect tools from accessible agents, enforcing per-workflow
        # access via WorkflowRepository (same gate the executor uses).
        tools: list[ToolInfo] = []
        seen_tool_ids: set[str] = set()  # Deduplicate across agents
        workflow_repo = self._build_workflow_repo(
            user_id=user_id, org_id=org_id, is_superuser=is_superuser
        )

        for agent in accessible_agents:
            # Add system tools from agent.system_tools
            for system_tool_id in agent.system_tools or []:
                if system_tool_id in seen_tool_ids:
                    continue
                seen_tool_ids.add(system_tool_id)

                # Get metadata from SYSTEM_TOOLS registry
                if system_tool_id in self._SYSTEM_TOOL_MAP:
                    tool_info = self._SYSTEM_TOOL_MAP[system_tool_id]
                    tools.append(tool_info)
                else:
                    # Unknown system tool - create basic info
                    logger.warning(f"Unknown system tool '{system_tool_id}' in agent '{agent.name}'")
                    tools.append(
                        ToolInfo(
                            id=system_tool_id,
                            name=system_tool_id.replace("_", " ").title(),
                            description=f"System tool: {system_tool_id}",
                            type="system",
                        )
                    )

            # Add workflow tools — gated per-workflow through the repo.
            for workflow_info in await self._visible_workflows_for_agent(
                agent, workflow_repo, seen_tool_ids
            ):
                tools.append(workflow_info)

        # Step 3: Apply global MCP config allowlist/blocklist
        config_service = MCPConfigService(self.session)
        config = await config_service.get_config()
        tools = self._apply_config_filters(tools, config)

        # Collect knowledge namespaces from accessible agents
        seen_namespaces: set[str] = set()
        for agent in accessible_agents:
            for ns in agent.knowledge_sources or []:
                seen_namespaces.add(ns)

        return MCPToolAccessResult(
            tools=tools,
            accessible_agent_ids=[agent.id for agent in accessible_agents],
            accessible_namespaces=list(seen_namespaces),
        )

    async def get_tools_for_agent(
        self,
        agent_id: UUID | str,
        user_roles: list[str],
        is_superuser: bool,
        user_id: UUID | str | None = None,
        org_id: UUID | str | None = None,
    ) -> AgentScopedToolResult | None:
        """
        Get MCP tools for a specific agent, verifying user access.

        Per-workflow visibility is enforced through ``WorkflowRepository`` —
        the same gate the executor uses. See ``get_accessible_tools`` for
        the reasoning on user_id/org_id requirements.

        Args:
            agent_id: The agent UUID to scope to
            user_roles: List of role names the user has (from JWT claims)
            is_superuser: Whether user is platform admin
            user_id: User UUID for per-workflow role check (from JWT claims).
            org_id: User's org UUID for per-workflow org scope check (from claims).

        Returns:
            AgentScopedToolResult if agent exists and user has access, None otherwise
        """
        # Query the specific agent with tools and roles eagerly loaded
        query = (
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.roles),
            )
            .where(Agent.id == str(agent_id))
            .where(Agent.is_active.is_(True))
        )

        result = await self.session.execute(query)
        agent = result.scalars().unique().first()

        if not agent:
            logger.warning(f"Agent {agent_id} not found or inactive")
            return None

        # Check access using same rules as _get_accessible_agents
        if not self._check_agent_access(agent, user_roles, is_superuser):
            logger.warning(f"User denied access to agent {agent_id}")
            return None

        # Collect tools from this agent
        tools: list[ToolInfo] = []

        for system_tool_id in agent.system_tools or []:
            if system_tool_id in self._SYSTEM_TOOL_MAP:
                tools.append(self._SYSTEM_TOOL_MAP[system_tool_id])
            else:
                logger.warning(f"Unknown system tool '{system_tool_id}' in agent '{agent.name}'")
                tools.append(
                    ToolInfo(
                        id=system_tool_id,
                        name=system_tool_id.replace("_", " ").title(),
                        description=f"System tool: {system_tool_id}",
                        type="system",
                    )
                )

        # Workflow tools — gated per-workflow through the repo (same gate as
        # the executor). seen_tool_ids is fresh because this is an
        # agent-scoped call: no cross-agent dedup needed.
        workflow_repo = self._build_workflow_repo(
            user_id=user_id, org_id=org_id, is_superuser=is_superuser
        )
        for workflow_info in await self._visible_workflows_for_agent(
            agent, workflow_repo, seen_tool_ids=set()
        ):
            tools.append(workflow_info)

        # Apply global config filters
        config_service = MCPConfigService(self.session)
        config = await config_service.get_config()
        tools = self._apply_config_filters(tools, config)

        # Collect knowledge namespaces
        namespaces = list(agent.knowledge_sources or [])

        return AgentScopedToolResult(
            tools=tools,
            agent_id=agent.id,
            agent_name=agent.name,
            system_prompt=agent.system_prompt,
            accessible_namespaces=namespaces,
        )

    def _build_workflow_repo(
        self,
        user_id: UUID | str | None,
        org_id: UUID | str | None,
        is_superuser: bool,
    ):
        """Construct a WorkflowRepository pinned to the caller's identity.

        OrgScopedRepository coerces string UUIDs internally (see commit
        9e892957), so passing JWT-claim strings here is safe.
        """
        # Local import — WorkflowRepository imports from models.orm at module
        # scope and we want to avoid pulling that into MCPToolAccessService's
        # import path.
        from src.repositories.workflows import WorkflowRepository

        return WorkflowRepository(
            self.session,
            org_id=org_id,
            user_id=user_id,
            is_superuser=is_superuser,
        )

    async def _visible_workflows_for_agent(
        self,
        agent: Agent,
        workflow_repo,
        seen_tool_ids: set[str],
    ) -> list[ToolInfo]:
        """Return ToolInfo for workflow tools the caller can access on this agent.

        For each workflow attached to ``agent.tools``, runs the same
        ``WorkflowRepository.get(id=...)`` gate the executor uses. Workflows
        the caller can't access (cross-org, role-gated without role,
        role_based-with-no-roles for non-superusers, etc.) are omitted —
        which means their names/descriptions/parameter schemas don't leak
        through ``tools/list`` either.
        """
        # Local import — see _build_workflow_repo for rationale.
        from src.services.mcp_server.server import get_registered_tool_name

        infos: list[ToolInfo] = []
        for workflow in agent.tools or []:
            workflow_id = str(workflow.id)
            if workflow_id in seen_tool_ids:
                continue

            # Same gate the executor uses: returns None if cross-org, missing
            # role, or any other access denial.
            accessible_workflow = await workflow_repo.get(id=workflow.id)
            if accessible_workflow is None:
                continue

            seen_tool_ids.add(workflow_id)
            registered_name = get_registered_tool_name(workflow_id)
            tool_id = registered_name if registered_name else workflow_id
            infos.append(
                ToolInfo(
                    id=tool_id,
                    name=accessible_workflow.name,
                    description=(
                        accessible_workflow.tool_description
                        or accessible_workflow.description
                        or ""
                    ),
                    type="workflow",
                    category=accessible_workflow.category,
                    default_enabled_for_coding_agent=False,
                )
            )
        return infos

    @staticmethod
    def _check_agent_access(
        agent: Agent,
        user_roles: list[str],
        is_superuser: bool,
    ) -> bool:
        """Check if user has access to a specific agent (same rules as _get_accessible_agents)."""
        if agent.access_level == AgentAccessLevel.AUTHENTICATED:
            return True

        if agent.access_level == AgentAccessLevel.ROLE_BASED:
            agent_role_names = {role.name for role in agent.roles}
            if not agent_role_names:
                return is_superuser
            return bool(set(user_roles) & agent_role_names)

        return False

    async def _get_accessible_agents(
        self,
        user_roles: list[str],
        is_superuser: bool,
    ) -> list[Agent]:
        """
        Get agents accessible to the user based on access_level and roles.

        Rules:
        - AUTHENTICATED: Any authenticated user can access
        - ROLE_BASED with roles: User must share at least one role with the agent
        - ROLE_BASED with no roles: Only superusers can access
        - Platform admins (superusers) are ALSO filtered by agent access (no bypass)
        """
        # Query all active agents with their tools and roles eagerly loaded
        query = (
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.roles),
            )
            .where(Agent.is_active.is_(True))
        )

        result = await self.session.execute(query)
        all_agents = result.scalars().unique().all()

        # Filter by access level
        accessible_agents: list[Agent] = []
        user_role_set = set(user_roles)

        for agent in all_agents:
            if agent.access_level == AgentAccessLevel.AUTHENTICATED:
                # Any authenticated user can access
                accessible_agents.append(agent)

            elif agent.access_level == AgentAccessLevel.ROLE_BASED:
                # Get role names from agent's roles
                agent_role_names = {role.name for role in agent.roles}

                if not agent_role_names:
                    # ROLE_BASED with no roles = only superusers can access
                    if is_superuser:
                        accessible_agents.append(agent)
                elif user_role_set & agent_role_names:
                    # User has at least one matching role
                    accessible_agents.append(agent)

            # Note: PUBLIC agents are not included for MCP access
            # as MCP requires authentication

        return accessible_agents

    def _apply_config_filters(
        self,
        tools: list[ToolInfo],
        config: MCPConfig,
    ) -> list[ToolInfo]:
        """Apply global allowlist/blocklist from MCP config."""
        # Apply allowlist (if set, only show tools in the list)
        if config.allowed_tool_ids:
            allowed_set = set(config.allowed_tool_ids)
            tools = [t for t in tools if t.id in allowed_set]

        # Apply blocklist (remove any blocked tools)
        if config.blocked_tool_ids:
            blocked_set = set(config.blocked_tool_ids)
            tools = [t for t in tools if t.id not in blocked_set]

        return tools
