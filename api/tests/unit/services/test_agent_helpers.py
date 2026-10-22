import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.services.execution.agent_helpers import (
    build_agent_system_prompt,
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
                "id": "bifrost_workflow_execute",
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
        mock_agent.system_tools = ["bifrost_workflow_execute"]
        mock_agent.knowledge_sources = []
        mock_agent.delegated_agents = []

        tools, id_map = await resolve_agent_tools(mock_agent, mock_session)
        assert isinstance(tools, list)
        assert isinstance(id_map, dict)
        assert len(tools) == 1
        assert tools[0].name == "bifrost_workflow_execute"

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

    @pytest.mark.asyncio
    @patch("src.services.mcp_server.server.get_system_tools", return_value=[])
    async def test_delegated_agents_do_not_create_synthetic_tool_definitions(
        self, _mock_get_system_tools
    ):
        """Delegation hardening must not change the model-facing tool contract."""
        delegated = MagicMock()
        delegated.name = "Echo Specialist"
        delegated.description = "Returns a concise specialist answer."
        delegated.is_active = True

        parent = MagicMock()
        parent.id = uuid4()
        parent.organization_id = None
        parent.tools = []
        parent.system_tools = []
        parent.knowledge_sources = []
        parent.delegated_agents = [delegated]

        tools, id_map = await resolve_agent_tools(parent, MagicMock())

        assert id_map == {}
        assert tools == []


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
        assert result == "Base prompt."  # Chat mode leaves the base prompt unchanged.
    def test_autonomous_mode_appends_suffix(self):
        """mode=autonomous appends the autonomous suffix."""
        mock_agent = MagicMock()
        mock_agent.system_prompt = "Base prompt."

        result = build_agent_system_prompt(mock_agent, execution_context={"mode": "autonomous"})
        assert result == "Base prompt." + AUTONOMOUS_MODE_SUFFIX
        assert "conclusive" in result
        assert "Do NOT ask questions" in result
        assert "exact callable name and input schema" in result
        assert "supplied by the runtime or returned by discovery" in result
        assert "Never guess a tool name, arguments, or schema" in result
        assert "reasonable alternatives" not in result
    def test_chat_mode_returns_prompt_verbatim(self):
        """mode=chat returns prompt unchanged (no suffix)."""
        mock_agent = MagicMock()
        mock_agent.system_prompt = "Base prompt."

        result = build_agent_system_prompt(mock_agent, execution_context={"mode": "chat"})
        assert result == "Base prompt."
