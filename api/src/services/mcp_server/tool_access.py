"""Compute MCP tool discovery with the same Agent and Workflow policies as REST."""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.log_safety import log_safe
from src.models.contracts.agents import ToolInfo
from src.models.orm.agents import Agent
from src.routers.tools import get_system_tools
from src.services.agent_write_policy import agent_query_in_reach, can_read_agent
from src.services.authorization.enforce import Caller, require_permission, user_reach
from src.services.mcp_server.config_service import MCPConfig, MCPConfigService

logger = logging.getLogger(__name__)


@dataclass
class MCPToolAccessResult:
    """Underlying tool inventory used by MCP access configuration."""

    tools: list[ToolInfo]


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
            if for_configuration:
                agent_id = str(agent.id)
                if agent_id not in seen:
                    seen.add(agent_id)
                    tools.append(ToolInfo(
                        id=agent_id,
                        name=f"Run {agent.name}",
                        description=agent.description or f"Run the {agent.name} agent.",
                        type="agent",
                    ))
            for tool in self._system_tools(agent):
                if tool.id not in seen:
                    seen.add(tool.id)
                    tools.append(tool)
            tools.extend(self.visible_workflows_for_agent(agent, caller, seen))
        if not for_configuration:
            config = await MCPConfigService(self.session).get_config()
            tools = self._apply_config_filters(tools, config)
        return MCPToolAccessResult(tools=tools)

    @staticmethod
    def _agent_query(caller: Caller):
        return agent_query_in_reach(caller).options(
            selectinload(Agent.tools), selectinload(Agent.roles)
        ).where(Agent.is_active.is_(True))

    async def _get_accessible_agents(self, caller: Caller) -> list[Agent]:
        candidates = (await self.session.execute(self._agent_query(caller))).scalars().unique().all()
        return [agent for agent in candidates if await can_read_agent(self.session, caller, agent)]

    @staticmethod
    def visible_workflows_for_agent(
        agent: Agent, caller: Caller, seen_tool_ids: set[str]
    ) -> list[ToolInfo]:
        """Share bound-workflow visibility between settings and live discovery."""
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
                id=workflow_id,
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
        if config.allowed_tool_ids is not None:
            allowed_set = set(config.allowed_tool_ids)
            tools = [t for t in tools if t.id in allowed_set]

        # Apply blocklist (remove any blocked tools)
        if config.blocked_tool_ids:
            blocked_set = set(config.blocked_tool_ids)
            tools = [t for t in tools if t.id not in blocked_set]

        return tools
