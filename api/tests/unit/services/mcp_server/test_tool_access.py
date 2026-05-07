"""
Unit tests for MCP Tool Access Service.

Tests the MCPToolAccessService which computes which MCP tools a user can access
based on their agent access permissions.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.enums import AgentAccessLevel
from src.services.mcp_server.tool_access import MCPToolAccessService


# ==================== Fixtures ====================


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    return AsyncMock()


@pytest.fixture
def service(mock_session):
    """Create an MCPToolAccessService instance."""
    return MCPToolAccessService(mock_session)


@pytest.fixture
def mock_role():
    """Create a mock Role object."""
    def _create_role(name: str):
        role = MagicMock()
        role.name = name
        return role
    return _create_role


@pytest.fixture
def mock_workflow():
    """Create a mock Workflow object."""
    def _create_workflow(name: str = "test_workflow", description: str = "Test workflow"):
        workflow = MagicMock()
        workflow.id = uuid4()
        workflow.name = name
        workflow.description = description
        workflow.tool_description = None
        workflow.category = "automation"
        return workflow
    return _create_workflow


@pytest.fixture
def mock_agent(mock_role, mock_workflow):
    """Create a mock Agent object."""
    def _create_agent(
        name: str = "Test Agent",
        access_level: AgentAccessLevel = AgentAccessLevel.AUTHENTICATED,
        system_tools: list[str] | None = None,
        knowledge_sources: list[str] | None = None,
        roles: list[str] | None = None,
        workflows: list | None = None,
    ):
        agent = MagicMock()
        agent.id = uuid4()
        agent.name = name
        agent.access_level = access_level
        agent.is_active = True
        agent.system_tools = system_tools or []
        agent.knowledge_sources = knowledge_sources or []
        agent.roles = [mock_role(r) for r in (roles or [])]
        agent.tools = workflows or []
        return agent
    return _create_agent


def mock_query_result(agents: list):
    """Create a mock query result with agents."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.unique.return_value.all.return_value = agents
    return mock_result


@pytest.fixture(autouse=True)
def patch_workflow_repo_passthrough():
    """Make the per-workflow gate a pass-through for unit tests by default.

    ``MCPToolAccessService`` now gates ``agent.tools`` through
    ``WorkflowRepository.get(id=...)`` so MCP listing uses the same access
    rule as the executor. Most existing unit tests mock the SQLAlchemy
    session at a low level and don't care about that gate — they assert
    that workflows attached to a visible agent appear in the result.

    Patch ``_build_workflow_repo`` to return a mock repo whose ``.get``
    looks up the workflow in the test's seeded agent fixtures and returns
    it. Tests that exercise the gate's deny semantics live in the e2e
    access matrix (``tests/e2e/mcp/test_mcp_tool_access_matrix.py``)
    against the real DB.
    """
    seen_workflows: dict = {}

    async def _fake_get(*, id, **_):
        return seen_workflows.get(id)

    fake_repo = AsyncMock()
    fake_repo.get = _fake_get

    def _build(self, **_):
        # Walk the test's mock-agent return value to populate the workflow
        # lookup. We grab whatever the latest session.execute call resolved
        # to (the agents list) and harvest all attached workflows.
        # Tests can use any depth of mock chaining (or none); if any link
        # in the chain isn't a MagicMock with the expected attribute, fall
        # through and leave seen_workflows empty — which gives the per-test
        # repo a deny-all default. That's safe: tests that need workflows
        # visible already populate the chain before calling the service.
        try:
            agents = (
                self.session.execute.return_value.scalars.return_value
                .unique.return_value.all.return_value
            )
            if isinstance(agents, list):
                for agent in agents:
                    for wf in (getattr(agent, "tools", None) or []):
                        seen_workflows[wf.id] = wf
        except AttributeError:
            # Mock chain didn't reach .all — leave seen_workflows empty.
            # Not an error condition, just "no agents seeded for this test".
            pass
        return fake_repo

    with patch.object(
        MCPToolAccessService, "_build_workflow_repo", autospec=True, side_effect=_build
    ) as mock_build:
        yield mock_build


# ==================== Agent Access Tests ====================


class TestGetAccessibleAgents:
    """Tests for _get_accessible_agents()."""

    @pytest.mark.asyncio
    async def test_authenticated_agents_accessible_to_all(self, service, mock_session, mock_agent):
        """AUTHENTICATED agents should be accessible to any authenticated user."""
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=["execute_workflow"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        result = await service._get_accessible_agents(
            user_roles=[],
            is_superuser=False,
        )

        assert len(result) == 1
        assert result[0].id == agent.id

    @pytest.mark.asyncio
    async def test_role_based_agent_with_matching_role(self, service, mock_session, mock_agent):
        """ROLE_BASED agent accessible when user has matching role."""
        agent = mock_agent(
            access_level=AgentAccessLevel.ROLE_BASED,
            system_tools=["list_workflows"],
            roles=["Developers"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        result = await service._get_accessible_agents(
            user_roles=["Developers"],
            is_superuser=False,
        )

        assert len(result) == 1
        assert result[0].id == agent.id

    @pytest.mark.asyncio
    async def test_role_based_agent_without_matching_role(self, service, mock_session, mock_agent):
        """ROLE_BASED agent not accessible when user lacks matching role."""
        agent = mock_agent(
            access_level=AgentAccessLevel.ROLE_BASED,
            system_tools=["list_workflows"],
            roles=["Admins"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        result = await service._get_accessible_agents(
            user_roles=["Developers"],  # User has different role
            is_superuser=False,
        )

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_role_based_agent_no_roles_superuser_access(self, service, mock_session, mock_agent):
        """ROLE_BASED agent with no roles accessible only to superusers."""
        agent = mock_agent(
            access_level=AgentAccessLevel.ROLE_BASED,
            system_tools=["execute_workflow"],
            roles=[],  # No roles assigned
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        # Non-superuser should NOT see it
        result = await service._get_accessible_agents(
            user_roles=["Developers"],
            is_superuser=False,
        )
        assert len(result) == 0

        # Superuser SHOULD see it
        result = await service._get_accessible_agents(
            user_roles=[],
            is_superuser=True,
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_platform_admin_still_filtered_by_agent_access(self, service, mock_session, mock_agent):
        """Platform admins should NOT bypass agent role requirements."""
        agent = mock_agent(
            access_level=AgentAccessLevel.ROLE_BASED,
            system_tools=["search_knowledge"],
            roles=["Secret Team"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        # Platform admin without the role should NOT see the agent
        result = await service._get_accessible_agents(
            user_roles=["Platform Admin"],  # Not "Secret Team"
            is_superuser=True,
        )

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_multiple_roles_matching(self, service, mock_session, mock_agent):
        """Agent accessible when any user role matches any agent role."""
        agent = mock_agent(
            access_level=AgentAccessLevel.ROLE_BASED,
            system_tools=["list_forms"],
            roles=["Developers", "QA"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        # User has QA role, agent has QA role - should match
        result = await service._get_accessible_agents(
            user_roles=["QA", "Support"],
            is_superuser=False,
        )

        assert len(result) == 1


# ==================== Tool Collection Tests ====================


class TestGetAccessibleTools:
    """Tests for get_accessible_tools()."""

    @pytest.mark.asyncio
    async def test_collects_system_tools_from_agents(self, service, mock_session, mock_agent):
        """Should collect system tools from accessible agents."""
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=["execute_workflow", "list_workflows"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch.object(service, '_apply_config_filters', side_effect=lambda t, c: t):
            with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
                mock_config = MagicMock()
                mock_config.allowed_tool_ids = None
                mock_config.blocked_tool_ids = None
                MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

                result = await service.get_accessible_tools(
                    user_roles=[],
                    is_superuser=True,
                )

        system_tools = [t for t in result.tools if t.type == "system"]
        assert len(system_tools) == 2
        tool_ids = {t.id for t in system_tools}
        assert tool_ids == {"execute_workflow", "list_workflows"}

    @pytest.mark.asyncio
    async def test_collects_workflow_tools_from_agents(self, service, mock_session, mock_agent, mock_workflow):
        """Should collect workflow tools from accessible agents."""
        workflow = mock_workflow(name="my_workflow", description="My workflow tool")
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=[],
            workflows=[workflow],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        workflow_tools = [t for t in result.tools if t.type == "workflow"]
        assert len(workflow_tools) == 1
        assert workflow_tools[0].name == "my_workflow"

    @pytest.mark.asyncio
    async def test_deduplicates_system_tools(self, service, mock_session, mock_agent):
        """System tools should be deduplicated across agents."""
        agent1 = mock_agent(
            name="Agent 1",
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=["execute_workflow", "list_workflows"],
        )
        agent2 = mock_agent(
            name="Agent 2",
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=["execute_workflow"],  # Duplicate
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent1, agent2]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=True,
            )

        # Should have 2 unique system tools, not 3
        system_tools = [t for t in result.tools if t.type == "system"]
        assert len(system_tools) == 2
        tool_ids = {t.id for t in system_tools}
        assert tool_ids == {"execute_workflow", "list_workflows"}

    @pytest.mark.asyncio
    async def test_deduplicates_workflow_tools(self, service, mock_session, mock_agent, mock_workflow):
        """Workflow tools should be deduplicated across agents."""
        workflow = mock_workflow(name="shared_workflow")

        agent1 = mock_agent(
            name="Agent 1",
            access_level=AgentAccessLevel.AUTHENTICATED,
            workflows=[workflow],
        )
        agent2 = mock_agent(
            name="Agent 2",
            access_level=AgentAccessLevel.AUTHENTICATED,
            workflows=[workflow],  # Same workflow
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent1, agent2]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        workflow_tools = [t for t in result.tools if t.type == "workflow"]
        assert len(workflow_tools) == 1

    @pytest.mark.asyncio
    async def test_returns_accessible_agent_ids(self, service, mock_session, mock_agent):
        """Should return list of accessible agent IDs."""
        agent1 = mock_agent(
            name="Agent 1",
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=["list_workflows"],
        )
        agent2 = mock_agent(
            name="Agent 2",
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=["list_forms"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent1, agent2]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        assert len(result.accessible_agent_ids) == 2
        assert agent1.id in result.accessible_agent_ids
        assert agent2.id in result.accessible_agent_ids


# ==================== Config Filtering Tests ====================


class TestApplyConfigFilters:
    """Tests for _apply_config_filters()."""

    def test_applies_config_blocklist(self, service):
        """Blocked tools should be removed."""
        from src.models.contracts.agents import ToolInfo

        tools = [
            ToolInfo(id="execute_workflow", name="Execute", description="", type="system"),
            ToolInfo(id="list_workflows", name="List", description="", type="system"),
            ToolInfo(id="search_knowledge", name="Search", description="", type="system"),
        ]

        mock_config = MagicMock()
        mock_config.allowed_tool_ids = None
        mock_config.blocked_tool_ids = ["search_knowledge"]

        result = service._apply_config_filters(tools, mock_config)

        tool_ids = {t.id for t in result}
        assert "search_knowledge" not in tool_ids
        assert "execute_workflow" in tool_ids
        assert "list_workflows" in tool_ids

    def test_applies_config_allowlist(self, service):
        """Only allowed tools should be returned when allowlist is set."""
        from src.models.contracts.agents import ToolInfo

        tools = [
            ToolInfo(id="execute_workflow", name="Execute", description="", type="system"),
            ToolInfo(id="list_workflows", name="List", description="", type="system"),
            ToolInfo(id="search_knowledge", name="Search", description="", type="system"),
        ]

        mock_config = MagicMock()
        mock_config.allowed_tool_ids = ["execute_workflow"]
        mock_config.blocked_tool_ids = None

        result = service._apply_config_filters(tools, mock_config)

        assert len(result) == 1
        assert result[0].id == "execute_workflow"

    def test_allowlist_and_blocklist_combined(self, service):
        """Blocklist should be applied after allowlist."""
        from src.models.contracts.agents import ToolInfo

        tools = [
            ToolInfo(id="execute_workflow", name="Execute", description="", type="system"),
            ToolInfo(id="list_workflows", name="List", description="", type="system"),
            ToolInfo(id="search_knowledge", name="Search", description="", type="system"),
        ]

        mock_config = MagicMock()
        mock_config.allowed_tool_ids = ["execute_workflow", "list_workflows"]
        mock_config.blocked_tool_ids = ["list_workflows"]

        result = service._apply_config_filters(tools, mock_config)

        # Allowlist filters to execute_workflow and list_workflows
        # Blocklist removes list_workflows
        assert len(result) == 1
        assert result[0].id == "execute_workflow"

    def test_no_filters_returns_all(self, service):
        """No filters should return all tools."""
        from src.models.contracts.agents import ToolInfo

        tools = [
            ToolInfo(id="execute_workflow", name="Execute", description="", type="system"),
            ToolInfo(id="list_workflows", name="List", description="", type="system"),
        ]

        mock_config = MagicMock()
        mock_config.allowed_tool_ids = None
        mock_config.blocked_tool_ids = None

        result = service._apply_config_filters(tools, mock_config)

        assert len(result) == 2

    def test_empty_allowlist_treated_as_no_filter(self, service):
        """Empty allowlist should be treated as no filter (same as None)."""
        from src.models.contracts.agents import ToolInfo

        tools = [
            ToolInfo(id="execute_workflow", name="Execute", description="", type="system"),
        ]

        mock_config = MagicMock()
        mock_config.allowed_tool_ids = []  # Empty list is falsy
        mock_config.blocked_tool_ids = None

        result = service._apply_config_filters(tools, mock_config)

        # Empty list is falsy, so no filter is applied - all tools returned
        assert len(result) == 1


# ==================== System Tool Metadata Tests ====================


class TestSystemToolMetadata:
    """Tests for system tool metadata mapping."""

    def test_known_system_tools_have_metadata(self, service):
        """Known system tools should have proper metadata."""
        expected_tools = [
            # Original tools
            "execute_workflow",
            "list_workflows",
            "list_integrations",
            "list_forms",
            "get_docs",
            "search_knowledge",
            # Code editor tools (precision editing)
            "list_content",
            "search_content",
            "read_content_lines",
            "get_content",
            "patch_content",
            "replace_content",
            "delete_content",
            # Workflow and execution tools
            "validate_workflow",
            "get_workflow",
            "list_executions",
            "get_execution",
        ]

        for tool_id in expected_tools:
            assert tool_id in service._SYSTEM_TOOL_MAP, f"Missing metadata for {tool_id}"
            tool = service._SYSTEM_TOOL_MAP[tool_id]
            assert tool.name, f"Tool {tool_id} missing name"
            assert tool.description, f"Tool {tool_id} missing description"

    @pytest.mark.asyncio
    async def test_unknown_system_tool_gets_basic_info(self, service, mock_session, mock_agent):
        """Unknown system tools should get basic auto-generated info."""
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=["unknown_custom_tool"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        assert len(result.tools) == 1
        tool = result.tools[0]
        assert tool.id == "unknown_custom_tool"
        assert tool.name == "Unknown Custom Tool"  # Auto-formatted
        assert "System tool" in tool.description


# ==================== Edge Cases ====================


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_no_accessible_agents_returns_empty(self, service, mock_session, mock_agent):
        """Should return empty list when no agents are accessible."""
        agent = mock_agent(
            access_level=AgentAccessLevel.ROLE_BASED,
            system_tools=["execute_workflow"],
            roles=["Secret Role"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=["Other Role"],
                is_superuser=False,
            )

        assert len(result.tools) == 0
        assert len(result.accessible_agent_ids) == 0

    @pytest.mark.asyncio
    async def test_agents_with_no_tools(self, service, mock_session, mock_agent):
        """Should handle agents with no tools configured."""
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=[],
            workflows=[],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        assert len(result.tools) == 0
        assert len(result.accessible_agent_ids) == 1  # Agent still accessible

    @pytest.mark.asyncio
    async def test_workflow_with_tool_description(self, service, mock_session, mock_agent, mock_workflow):
        """Should use tool_description if available over description."""
        workflow = mock_workflow(name="my_tool", description="General description")
        workflow.tool_description = "Specific tool description"

        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            workflows=[workflow],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        workflow_tool = [t for t in result.tools if t.type == "workflow"][0]
        assert workflow_tool.description == "Specific tool description"


# ==================== Knowledge Namespace Access Tests ====================


class TestKnowledgeNamespaceAccess:
    """Tests for accessible_namespaces in MCPToolAccessResult."""

    @pytest.mark.asyncio
    async def test_collects_namespaces_from_accessible_agents(self, service, mock_session, mock_agent):
        """Should collect knowledge_sources from accessible agents."""
        agent1 = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            knowledge_sources=["docs", "faq"],
        )
        agent2 = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            knowledge_sources=["tutorials"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent1, agent2]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        assert set(result.accessible_namespaces) == {"docs", "faq", "tutorials"}

    @pytest.mark.asyncio
    async def test_deduplicates_namespaces_across_agents(self, service, mock_session, mock_agent):
        """Should deduplicate namespaces when multiple agents share the same namespace."""
        agent1 = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            knowledge_sources=["shared-ns", "unique-1"],
        )
        agent2 = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            knowledge_sources=["shared-ns", "unique-2"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent1, agent2]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        # Should have exactly 3 unique namespaces
        assert len(result.accessible_namespaces) == 3
        assert set(result.accessible_namespaces) == {"shared-ns", "unique-1", "unique-2"}

    @pytest.mark.asyncio
    async def test_returns_empty_namespaces_when_no_agents_accessible(self, service, mock_session):
        """Should return empty accessible_namespaces when no agents are accessible."""
        mock_session.execute = AsyncMock(return_value=mock_query_result([]))

        with patch('src.services.mcp_server.tool_access.MCPConfigService') as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=False,
            )

        assert result.accessible_namespaces == []


# ==================== search_knowledge Auto-Injection Tests ====================


class TestSearchKnowledgeAutoInjection:
    """search_knowledge must be auto-injected when an agent has knowledge_sources.

    Mirrors the native chat path in agent_helpers.py so MCP listing matches
    what the agent executor exposes.
    """

    @pytest.mark.asyncio
    async def test_get_accessible_tools_injects_search_knowledge(
        self, service, mock_session, mock_agent
    ):
        """get_accessible_tools includes search_knowledge when agent has
        knowledge_sources, even if system_tools doesn't list it."""
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=[],  # explicitly empty
            knowledge_sources=["docs"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch("src.services.mcp_server.tool_access.MCPConfigService") as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=True,
            )

        tool_ids = {t.id for t in result.tools}
        assert "search_knowledge" in tool_ids

    @pytest.mark.asyncio
    async def test_get_accessible_tools_does_not_inject_without_namespaces(
        self, service, mock_session, mock_agent
    ):
        """No auto-injection when knowledge_sources is empty."""
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=[],
            knowledge_sources=[],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch("src.services.mcp_server.tool_access.MCPConfigService") as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=True,
            )

        tool_ids = {t.id for t in result.tools}
        assert "search_knowledge" not in tool_ids

    @pytest.mark.asyncio
    async def test_get_accessible_tools_no_duplicate_when_explicit(
        self, service, mock_session, mock_agent
    ):
        """Auto-injection must not duplicate search_knowledge if already
        listed in system_tools explicitly."""
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=["search_knowledge"],
            knowledge_sources=["docs"],
        )

        mock_session.execute = AsyncMock(return_value=mock_query_result([agent]))

        with patch("src.services.mcp_server.tool_access.MCPConfigService") as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_accessible_tools(
                user_roles=[],
                is_superuser=True,
            )

        sk_tools = [t for t in result.tools if t.id == "search_knowledge"]
        assert len(sk_tools) == 1

    @pytest.mark.asyncio
    async def test_get_tools_for_agent_injects_search_knowledge(
        self, service, mock_session, mock_agent
    ):
        """get_tools_for_agent (the agent-scoped path) auto-injects too."""
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=[],
            knowledge_sources=["docs"],
        )
        agent.system_prompt = "You search docs."

        # get_tools_for_agent uses scalars().unique().first() (single agent),
        # not the .all() shape used elsewhere in this file.
        mock_result = MagicMock()
        mock_result.scalars.return_value.unique.return_value.first.return_value = agent
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.mcp_server.tool_access.MCPConfigService") as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_tools_for_agent(
                agent_id=agent.id,
                user_roles=[],
                is_superuser=True,
            )

        assert result is not None
        tool_ids = {t.id for t in result.tools}
        assert "search_knowledge" in tool_ids

    @pytest.mark.asyncio
    async def test_get_tools_for_agent_no_inject_without_namespaces(
        self, service, mock_session, mock_agent
    ):
        agent = mock_agent(
            access_level=AgentAccessLevel.AUTHENTICATED,
            system_tools=[],
            knowledge_sources=[],
        )
        agent.system_prompt = ""

        mock_result = MagicMock()
        mock_result.scalars.return_value.unique.return_value.first.return_value = agent
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.mcp_server.tool_access.MCPConfigService") as MockConfig:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            MockConfig.return_value.get_config = AsyncMock(return_value=mock_config)

            result = await service.get_tools_for_agent(
                agent_id=agent.id,
                user_roles=[],
                is_superuser=True,
            )

        assert result is not None
        tool_ids = {t.id for t in result.tools}
        assert "search_knowledge" not in tool_ids
