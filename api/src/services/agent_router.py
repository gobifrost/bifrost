"""
Agent Router Service

Routes user messages to the appropriate agent based on:
1. @mention syntax (explicit routing)
2. AI-based intent analysis (automatic routing)
"""

import logging
import re
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType
from src.models.orm import Agent
from src.repositories.agents import AgentRepository
from src.services.llm import get_llm_client, LLMMessage

logger = logging.getLogger(__name__)


# Regex to match @[Agent Name] mentions (bracketed format).
# Length cap (1..256) prevents pathological backtracking on attacker-supplied
# chat messages that omit the closing bracket.
MENTION_PATTERN = re.compile(r"@\[([^\]]{1,256})\]")


class AgentRouter:
    """
    Routes chat messages to appropriate agents.

    Supports two routing modes:
    1. Explicit: User types @AgentName to switch to that agent
    2. Automatic: AI analyzes message intent and routes to best-fit agent
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        user_id: UUID | None = None,
        org_id: UUID | None = None,
        is_superuser: bool = False,
    ):
        self._session_factory = session_factory
        self._user_id = user_id
        self._org_id = org_id
        self._is_superuser = is_superuser

    @asynccontextmanager
    async def _db(self):
        """Short-lived DB session for discrete operations."""
        async with self._session_factory() as session:
            yield session
            await session.commit()

    async def parse_mention(
        self,
        message: str,
        available_agents: list[Agent] | None = None,
    ) -> Agent | None:
        """
        Parse @mention from user message and find matching agent.

        Args:
            message: User's message text

        Returns:
            Agent if a valid @mention was found, None otherwise
        """
        match = MENTION_PATTERN.search(message)
        if not match:
            return None

        agent_name = match.group(1).strip()

        if available_agents is None:
            available_agents = await self.get_available_agents()

        for agent in available_agents:
            if agent.name.lower() == agent_name.lower():
                logger.info(f"@mention routing to agent: {agent.name}")
                return agent

        logger.info("@mention did not match an accessible agent: %s", log_safe(agent_name))
        return None

    def _build_agent_description(self, agent: Agent) -> str:
        """Build agent description including tool and knowledge capabilities for routing."""
        description = agent.description or "General assistant"

        # Get tool names for this agent
        tool_names = []
        for tool in agent.tools:
            if tool.is_active and tool.type == "tool":
                tool_names.append(tool.name)

        # Build capability strings
        capabilities = []
        if tool_names:
            capabilities.append(f"Tools: {', '.join(tool_names)}")
        if agent.knowledge_sources:
            capabilities.append(f"Knowledge: {', '.join(agent.knowledge_sources)}")

        if capabilities:
            return f"- {agent.name}: {description} ({'; '.join(capabilities)})"
        return f"- {agent.name}: {description}"

    async def route_message(
        self,
        message: str,
        available_agents: list[Agent] | None = None,
    ) -> Agent | None:
        """
        Use AI to route a message to the most appropriate agent.

        Args:
            message: User's message text
            available_agents: Optional list of agents to consider (defaults to all active)

        Returns:
            Agent if a good match was found, None to handle directly
        """
        # Get available agents if not provided (with tools and delegations eager-loaded)
        if available_agents is None:
            available_agents = await self.get_available_agents()

        # If no agents available, return None
        if not available_agents:
            return None

        # Build agent descriptions with tool and knowledge info for better routing
        agent_descriptions = "\n".join([
            self._build_agent_description(agent)
            for agent in available_agents
        ])

        router_prompt = f"""You are a routing assistant. Analyze the user's message and determine which agent (if any) is best suited to handle their request.

Available agents:
{agent_descriptions}

Rules:
1. If the user's request matches an agent's specialty, tools, or knowledge sources, respond with ONLY the agent name (exactly as shown above).
2. If the request is general or doesn't match any agent specialty, respond with "DIRECT".
3. When in doubt, prefer routing to a specialist if their tools or knowledge could help.
4. If a user asks about data that matches an agent's knowledge source (e.g., "tickets" matches "halopsa-tickets"), route to that agent.
5. For "Coding Assistant" specifically, route requests that involve:
   - Creating, building, or developing workflows, automations, or integrations
   - Writing or modifying code/scripts for the platform
   - SDK or API development questions
   - Questions about Bifrost SDK patterns or capabilities

User message: {message}

Your response (agent name or DIRECT):"""

        logger.debug(f"Router prompt:\n{log_safe(router_prompt, max_len=2000)}")

        try:
            async with self._db() as session:
                llm_client = await get_llm_client(session)

            # Use non-streaming for quick routing decision
            # Don't pass temperature or max_tokens — some models (e.g. OpenAI o-series)
            # reject temperature, and a low max_tokens can truncate responses with preamble.
            response = await llm_client.complete(
                messages=[
                    LLMMessage(role="system", content="You are a routing assistant. Respond only with the agent name or DIRECT."),
                    LLMMessage(role="user", content=router_prompt),
                ],
            )

            if response.content:
                agent_name = response.content.strip()
                logger.debug(f"Router LLM response: '{agent_name}'")

                if agent_name.upper() == "DIRECT":
                    return None

                # Find matching agent
                for agent in available_agents:
                    if agent.name.lower() == agent_name.lower():
                        logger.info(f"AI routing to agent: {agent.name}")
                        return agent

                logger.warning(
                    f"Router response '{agent_name}' did not match any agent. "
                    f"Available: {[a.name for a in available_agents]}"
                )
            else:
                logger.warning("Router LLM returned empty response")

            return None

        except Exception as e:
            logger.error(f"Agent routing failed: {e}", exc_info=True)
            return None

    async def get_available_agents(self) -> list[Agent]:
        """Get active agents available to the current user for routing."""
        if self._user_id is None:
            logger.warning("Agent routing requested without user context; no agents are routable")
            return []

        async with self._db() as session:
            repo = AgentRepository(
                session=session,
                org_id=self._org_id,
                user_id=self._user_id,
                is_superuser=self._is_superuser,
            )
            if self._is_superuser:
                return await repo.list_all_in_scope(
                    filter_type=OrgFilterType.ALL,
                    active_only=True,
                )
            return await repo.list_agents(active_only=True)

    def strip_mention(self, message: str) -> str:
        """
        Remove @mention from message for cleaner processing.

        Args:
            message: Original message with @mention

        Returns:
            Message with @mention removed
        """
        return MENTION_PATTERN.sub("", message).strip()
