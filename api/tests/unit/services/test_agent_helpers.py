import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.services.execution.agent_helpers import (
    agent_delegation_slug,
    build_agent_system_prompt,
    find_delegated_agent,
    resolve_agent_tools,
    AUTONOMOUS_MODE_SUFFIX,
)


class TestResolveAgentTools:
    @pytest.mark.asyncio
    @patch("src.services.mcp_server.server.get_system_tools")
    async def test_returns_tool_definitions(self, mock_get_system_tools):
        """resolve_agent_tools returns tool definitions from agent config."""
        mock_get_system_tools.return_value = [
            {
                "id": "execute_workflow",
                "description": "Execute a workflow",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        mock_session = MagicMock()
        mock_agent = MagicMock()
        mock_agent.id = uuid4()
        # organization_id=None skips the MCP-tool query path so this test
        # stays focused on the system-tool tier without needing a real DB.
        mock_agent.organization_id = None
        mock_agent.tools = []
        mock_agent.system_tools = ["execute_workflow"]
        mock_agent.knowledge_sources = []
        mock_agent.delegated_agents = []

        tools, id_map = await resolve_agent_tools(mock_agent, mock_session)
        assert isinstance(tools, list)
        assert isinstance(id_map, dict)
        assert len(tools) == 1
        assert tools[0].name == "execute_workflow"

    @pytest.mark.asyncio
    @patch("src.services.mcp_server.server.get_system_tools")
    async def test_adds_search_knowledge_when_sources_exist(self, mock_get_system_tools):
        """Auto-adds search_knowledge tool when agent has knowledge sources."""
        mock_get_system_tools.return_value = [
            {
                "id": "search_knowledge",
                "description": "Search knowledge",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        mock_session = MagicMock()
        mock_agent = MagicMock()
        mock_agent.id = uuid4()
        # organization_id=None skips the MCP-tool query path so this test
        # stays focused on the system-tool tier without needing a real DB.
        mock_agent.organization_id = None
        mock_agent.tools = []
        mock_agent.system_tools = []
        mock_agent.knowledge_sources = ["docs"]
        mock_agent.delegated_agents = []

        tools, _ = await resolve_agent_tools(mock_agent, mock_session)
        tool_names = [t.name for t in tools]
        assert "search_knowledge" in tool_names

    @pytest.mark.asyncio
    @patch("src.services.mcp_server.server.get_system_tools", return_value=[])
    async def test_no_tools_returns_empty(self, _mock_get_system_tools):
        """Agent with no tools returns empty lists."""
        mock_session = MagicMock()
        mock_agent = MagicMock()
        mock_agent.id = uuid4()
        # organization_id=None skips the MCP-tool query path so this test
        # stays focused on the system-tool tier without needing a real DB.
        mock_agent.organization_id = None
        mock_agent.tools = []
        mock_agent.system_tools = []
        mock_agent.knowledge_sources = []
        mock_agent.delegated_agents = []

        tools, id_map = await resolve_agent_tools(mock_agent, mock_session)
        assert tools == []
        assert id_map == {}


class TestBuildAgentSystemPrompt:
    def test_uses_agent_system_prompt(self):
        """Uses the agent's configured system prompt."""
        mock_agent = MagicMock()
        mock_agent.system_prompt = "You are a helpful assistant."

        result = build_agent_system_prompt(mock_agent)
        assert result == "You are a helpful assistant."

    def test_no_context_returns_prompt_verbatim(self):
        """No execution_context returns prompt unchanged."""
        mock_agent = MagicMock()
        mock_agent.system_prompt = "Base prompt."

        result = build_agent_system_prompt(mock_agent, execution_context=None)
        assert result == "Base prompt."

    def test_autonomous_mode_appends_suffix(self):
        """mode=autonomous appends the autonomous suffix."""
        mock_agent = MagicMock()
        mock_agent.system_prompt = "Base prompt."

        result = build_agent_system_prompt(mock_agent, execution_context={"mode": "autonomous"})
        assert result == "Base prompt." + AUTONOMOUS_MODE_SUFFIX
        assert "conclusive" in result
        assert "Do NOT ask questions" in result

    def test_chat_mode_returns_prompt_verbatim(self):
        """mode=chat returns prompt unchanged (no suffix)."""
        mock_agent = MagicMock()
        mock_agent.system_prompt = "Base prompt."

        result = build_agent_system_prompt(mock_agent, execution_context={"mode": "chat"})
        assert result == "Base prompt."


class TestAgentDelegationSlug:
    def test_simple_name(self):
        assert agent_delegation_slug("Reporter") == "delegate_to_reporter"

    def test_name_with_spaces(self):
        assert agent_delegation_slug("Data Analyst") == "delegate_to_data_analyst"

    def test_mixed_case(self):
        assert agent_delegation_slug("My Cool Agent") == "delegate_to_my_cool_agent"


class TestFindDelegatedAgent:
    def _make_agent(self, name: str, is_active: bool = True):
        agent = MagicMock()
        agent.name = name
        agent.is_active = is_active
        return agent

    def test_finds_matching_agent(self):
        parent = MagicMock()
        child = self._make_agent("Data Analyst")
        parent.delegated_agents = [child]

        result = find_delegated_agent(parent, "delegate_to_data_analyst")
        assert result is child

    def test_returns_none_for_no_match(self):
        parent = MagicMock()
        parent.delegated_agents = [self._make_agent("Reporter")]

        result = find_delegated_agent(parent, "delegate_to_nonexistent")
        assert result is None

    def test_skips_inactive_agent(self):
        parent = MagicMock()
        parent.delegated_agents = [self._make_agent("Reporter", is_active=False)]

        result = find_delegated_agent(parent, "delegate_to_reporter")
        assert result is None

    def test_handles_no_delegated_agents(self):
        parent = MagicMock()
        parent.delegated_agents = None

        result = find_delegated_agent(parent, "delegate_to_anything")
        assert result is None

    def test_handles_empty_list(self):
        parent = MagicMock()
        parent.delegated_agents = []

        result = find_delegated_agent(parent, "delegate_to_anything")
        assert result is None

    def test_multiple_agents_returns_correct_one(self):
        parent = MagicMock()
        a1 = self._make_agent("Reporter")
        a2 = self._make_agent("Data Analyst")
        parent.delegated_agents = [a1, a2]

        result = find_delegated_agent(parent, "delegate_to_data_analyst")
        assert result is a2
