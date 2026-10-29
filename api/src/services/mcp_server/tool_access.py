"""Compute MCP tool discovery with the same Agent and Workflow policies as REST."""

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.log_safety import log_safe
from src.models.contracts.agents import ToolInfo
from src.models.orm.agents import Agent
from src.routers.tools import get_system_tools
from src.services.agent_write_policy import agent_query_in_reach, can_read_agent
from shared.scope_resolver import shared_read_organization
from src.services.authorization.enforce import Caller, require_permission, user_reach
from src.services.mcp_server.config_service import MCPConfig, MCPConfigService

logger = logging.getLogger(__name__)


@dataclass
class MCPToolAccessResult:
    """Underlying tool inventory used by MCP access configuration."""

    tools: list[ToolInfo]


@dataclass
class AgentScopedToolResult:
    """Tool inventory for an Agent the original caller may access."""

    tools: list[ToolInfo]
    accessible_namespaces: list[str]


class MCPToolAccessService:
    """Discover only reachable Agents, preserving their resource ACLs.

    Copied role names and identity flags are never authorization inputs.
    Every caller supplies current database-backed authority. Configuration
    reads separately require the Global MCP read permission.
    """

    _SYSTEM_TOOL_MAP: dict[str, ToolInfo] = {tool.id: tool for tool in get_system_tools()}

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_accessible_tools(
        self, caller: Caller, *, for_configuration: bool = False
    ) -> MCPToolAccessResult:
        if for_configuration:
            require_permission(caller, "mcp.read", None)
        agents = await self._get_accessible_agents(caller)
        tools: list[ToolInfo] = []
        seen: set[str] = set()
        for agent in agents:
            for tool in self._system_tools(agent):
                if tool.id not in seen:
                    seen.add(tool.id)
                    tools.append(tool)
            tools.extend(self._visible_workflows_for_agent(agent, caller, seen))
        if not for_configuration:
            config = await MCPConfigService(self.session).get_config()
            tools = self._apply_config_filters(tools, config)
        return MCPToolAccessResult(tools=tools)

    async def get_tools_for_agent(
        self, agent_id: UUID | str, caller: Caller, *, for_execution: bool = False
    ) -> AgentScopedToolResult | None:
        try:
            identity = UUID(str(agent_id))
        except (TypeError, ValueError):
            return None
        query = self._agent_query(caller).where(Agent.id == identity)
        agent = (await self.session.execute(query)).scalars().unique().first()
        if agent is None or not await can_read_agent(self.session, caller, agent):
            return None
        if for_execution:
            require_permission(
                caller, "agents.execute",
                shared_read_organization(caller, agent.organization_id),
            )
        tools = self._system_tools(agent)
        tools.extend(
            self._visible_workflows_for_agent(
                agent, caller, {tool.id for tool in tools}
            )
        )
        config = await MCPConfigService(self.session).get_config()
        return AgentScopedToolResult(
            tools=self._apply_config_filters(tools, config),
            accessible_namespaces=list(agent.knowledge_sources or []),
        )

    @staticmethod
    def _agent_query(caller: Caller):
        return agent_query_in_reach(caller).options(
            selectinload(Agent.tools), selectinload(Agent.roles)
        ).where(Agent.is_active.is_(True))

    async def _get_accessible_agents(self, caller: Caller) -> list[Agent]:
        candidates = (await self.session.execute(self._agent_query(caller))).scalars().unique().all()
        return [agent for agent in candidates if await can_read_agent(self.session, caller, agent)]

    def _visible_workflows_for_agent(
        self, agent: Agent, caller: Caller, seen_tool_ids: set[str]
    ) -> list[ToolInfo]:
        from src.services.mcp_server.server import get_registered_tool_name

        infos: list[ToolInfo] = []
        for workflow in agent.tools or []:
            workflow_id = str(workflow.id)
            if workflow_id in seen_tool_ids:
                continue
            # Agent sharing is the resource gate for its explicitly bound
            # tools. A tool does not add the direct workflow role ACL.
            if (
                not workflow.is_active or workflow.type != "tool"
                or not user_reach(caller).covers(workflow.organization_id)
            ):
                continue
            seen_tool_ids.add(workflow_id)
            infos.append(ToolInfo(
                id=get_registered_tool_name(workflow_id) or workflow_id,
                name=workflow.name,
                description=workflow.tool_description or workflow.description or "",
                type="workflow",
                category=workflow.category,
                default_enabled_for_coding_agent=False,
            ))
        return infos

    def _system_tools(self, agent: Agent) -> list[ToolInfo]:
        tools: list[ToolInfo] = []
        for tool_id in self._effective_system_tool_ids(agent):
            if tool_id in self._SYSTEM_TOOL_MAP:
                tools.append(self._SYSTEM_TOOL_MAP[tool_id])
            else:
                logger.warning("Unknown system tool %s on Agent %s", log_safe(tool_id), log_safe(agent.name))
                tools.append(ToolInfo(
                    id=tool_id,
                    name=tool_id.replace("_", " ").title(),
                    description=f"System tool: {tool_id}",
                    type="system",
                ))
        return tools

    @staticmethod
    def _effective_system_tool_ids(agent: Agent) -> list[str]:
        """Match the native executor's automatic knowledge-search tool."""
        ids = list(agent.system_tools or [])
        if agent.knowledge_sources and "search_knowledge" not in ids:
            ids.append("search_knowledge")
        return ids

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
