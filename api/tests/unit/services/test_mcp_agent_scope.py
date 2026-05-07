"""
Unit tests for agent-scoped MCP endpoint (/mcp/{agent_id}).

Tests:
- AgentScopeMCPMiddleware: ASGI path rewriting
- MCPToolAccessService.get_tools_for_agent: Agent-scoped tool access
- ToolFilterMiddleware: Agent-scoped filtering (on_initialize, on_list_tools, on_call_tool)
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.agent_scope import AgentScopeMCPMiddleware


# =============================================================================
# AgentScopeMCPMiddleware Tests
# =============================================================================


class TestAgentScopeMCPMiddleware:
    """Tests for the ASGI path-rewriting middleware."""

    @pytest.fixture
    def app(self):
        """Mock ASGI app that records scope."""
        async def mock_app(scope, receive, send):
            mock_app.last_scope = scope
        mock_app.last_scope = None
        return mock_app

    @pytest.fixture
    def middleware(self, app):
        return AgentScopeMCPMiddleware(app)

    @pytest.mark.asyncio
    async def test_rewrites_uuid_path(self, middleware, app):
        """Path /mcp/{uuid} is rewritten to /mcp with agent_id in scope."""
        agent_id = "12345678-1234-1234-1234-123456789abc"
        scope = {"type": "http", "path": f"/mcp/{agent_id}"}

        await middleware(scope, None, None)

        assert app.last_scope["path"] == "/mcp"
        assert app.last_scope["mcp_agent_id"] == agent_id

    @pytest.mark.asyncio
    async def test_preserves_trailing_path(self, middleware, app):
        """Path /mcp/{uuid}/sse is rewritten to /mcp/sse."""
        agent_id = "12345678-1234-1234-1234-123456789abc"
        scope = {"type": "http", "path": f"/mcp/{agent_id}/sse"}

        await middleware(scope, None, None)

        assert app.last_scope["path"] == "/mcp/sse"
        assert app.last_scope["mcp_agent_id"] == agent_id

    @pytest.mark.asyncio
    async def test_ignores_callback_path(self, middleware, app):
        """Path /mcp/callback is NOT rewritten (not a UUID)."""
        scope = {"type": "http", "path": "/mcp/callback"}

        await middleware(scope, None, None)

        assert app.last_scope["path"] == "/mcp/callback"
        assert "mcp_agent_id" not in app.last_scope

    @pytest.mark.asyncio
    async def test_ignores_plain_mcp_path(self, middleware, app):
        """Path /mcp (no agent_id) passes through unchanged."""
        scope = {"type": "http", "path": "/mcp"}

        await middleware(scope, None, None)

        assert app.last_scope["path"] == "/mcp"
        assert "mcp_agent_id" not in app.last_scope

    @pytest.mark.asyncio
    async def test_ignores_non_uuid_suffix(self, middleware, app):
        """Path /mcp/not-a-uuid passes through unchanged."""
        scope = {"type": "http", "path": "/mcp/not-a-uuid"}

        await middleware(scope, None, None)

        assert app.last_scope["path"] == "/mcp/not-a-uuid"
        assert "mcp_agent_id" not in app.last_scope

    @pytest.mark.asyncio
    async def test_ignores_non_http_scope(self, middleware, app):
        """Non-HTTP scopes (like lifespan) pass through unchanged."""
        scope = {"type": "lifespan", "path": "/mcp/12345678-1234-1234-1234-123456789abc"}

        await middleware(scope, None, None)

        assert app.last_scope["path"] == "/mcp/12345678-1234-1234-1234-123456789abc"
        assert "mcp_agent_id" not in app.last_scope

    @pytest.mark.asyncio
    async def test_case_insensitive_uuid(self, middleware, app):
        """UUIDs with uppercase letters are matched correctly."""
        agent_id = "12345678-1234-1234-1234-123456789ABC"
        scope = {"type": "http", "path": f"/mcp/{agent_id}"}

        await middleware(scope, None, None)

        assert app.last_scope["path"] == "/mcp"
        assert app.last_scope["mcp_agent_id"] == agent_id


# =============================================================================
# MCPToolAccessService.get_tools_for_agent Tests
# =============================================================================


class TestGetToolsForAgent:
    """Tests for agent-scoped tool access."""

    @pytest.fixture
    def mock_agent(self):
        """Create a mock agent."""
        agent = MagicMock()
        agent.id = uuid4()
        agent.name = "Test Agent"
        agent.system_prompt = "You are a helpful assistant."
        agent.access_level = MagicMock()
        agent.access_level.__eq__ = lambda self, other: str(self) == str(other)
        agent.system_tools = ["execute_workflow", "list_workflows"]
        agent.tools = []
        agent.roles = []
        agent.knowledge_sources = ["docs", "wiki"]
        agent.is_active = True
        return agent

    @pytest.mark.asyncio
    async def test_returns_tools_for_accessible_agent(self, mock_agent):
        """Returns tools when user has access to the agent."""
        from src.models.enums import AgentAccessLevel

        mock_agent.access_level = AgentAccessLevel.AUTHENTICATED

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.unique.return_value.first.return_value = mock_agent
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.mcp_server.tool_access.MCPConfigService"
        ) as mock_config_cls:
            mock_config = MagicMock()
            mock_config.allowed_tool_ids = None
            mock_config.blocked_tool_ids = None
            mock_config_cls.return_value.get_config = AsyncMock(return_value=mock_config)

            from src.services.mcp_server.tool_access import MCPToolAccessService

            service = MCPToolAccessService(mock_session)
            result = await service.get_tools_for_agent(
                agent_id=mock_agent.id,
                user_roles=["admin"],
                is_superuser=False,
            )

        assert result is not None
        assert result.agent_id == mock_agent.id
        assert result.agent_name == "Test Agent"
        assert result.system_prompt == "You are a helpful assistant."
        assert result.accessible_namespaces == ["docs", "wiki"]
        # execute_workflow and list_workflows from system_tools, plus
        # search_knowledge auto-injected because knowledge_sources is non-empty
        # (mirrors the native chat path in agent_helpers.py).
        tool_ids = {t.id for t in result.tools}
        assert tool_ids == {"execute_workflow", "list_workflows", "search_knowledge"}

    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent_agent(self):
        """Returns None when agent doesn't exist."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.unique.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        from src.services.mcp_server.tool_access import MCPToolAccessService

        service = MCPToolAccessService(mock_session)
        result = await service.get_tools_for_agent(
            agent_id=uuid4(),
            user_roles=["admin"],
            is_superuser=False,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_access_denied(self, mock_agent):
        """Returns None when user doesn't have access to the agent."""
        from src.models.enums import AgentAccessLevel

        mock_agent.access_level = AgentAccessLevel.ROLE_BASED
        mock_role = MagicMock()
        mock_role.name = "special_role"
        mock_agent.roles = [mock_role]

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.unique.return_value.first.return_value = mock_agent
        mock_session.execute = AsyncMock(return_value=mock_result)

        from src.services.mcp_server.tool_access import MCPToolAccessService

        service = MCPToolAccessService(mock_session)
        result = await service.get_tools_for_agent(
            agent_id=mock_agent.id,
            user_roles=["other_role"],  # Doesn't match agent's role
            is_superuser=False,
        )

        assert result is None


# =============================================================================
# MCPToolAccessService._check_agent_access Tests
# =============================================================================


class TestCheckAgentAccess:
    """Tests for static agent access checking."""

    def test_authenticated_access_level(self):
        """AUTHENTICATED agents are accessible to any user."""
        from src.models.enums import AgentAccessLevel
        from src.services.mcp_server.tool_access import MCPToolAccessService

        agent = MagicMock()
        agent.access_level = AgentAccessLevel.AUTHENTICATED
        agent.roles = []

        assert MCPToolAccessService._check_agent_access(agent, [], False) is True
        assert MCPToolAccessService._check_agent_access(agent, ["admin"], True) is True

    def test_role_based_with_matching_role(self):
        """ROLE_BASED agents are accessible when user has a matching role."""
        from src.models.enums import AgentAccessLevel
        from src.services.mcp_server.tool_access import MCPToolAccessService

        agent = MagicMock()
        agent.access_level = AgentAccessLevel.ROLE_BASED
        mock_role = MagicMock()
        mock_role.name = "admin"
        agent.roles = [mock_role]

        assert MCPToolAccessService._check_agent_access(agent, ["admin"], False) is True
        assert MCPToolAccessService._check_agent_access(agent, ["user"], False) is False

    def test_role_based_no_roles_superuser_only(self):
        """ROLE_BASED with no roles = only superusers can access."""
        from src.models.enums import AgentAccessLevel
        from src.services.mcp_server.tool_access import MCPToolAccessService

        agent = MagicMock()
        agent.access_level = AgentAccessLevel.ROLE_BASED
        agent.roles = []

        assert MCPToolAccessService._check_agent_access(agent, [], True) is True
        assert MCPToolAccessService._check_agent_access(agent, [], False) is False

    def test_unknown_access_level_denied(self):
        """Unknown access levels are denied."""
        from src.services.mcp_server.tool_access import MCPToolAccessService

        agent = MagicMock()
        agent.access_level = "unknown"
        agent.roles = []

        assert MCPToolAccessService._check_agent_access(agent, [], False) is False
        assert MCPToolAccessService._check_agent_access(agent, [], True) is False


# =============================================================================
# ToolFilterMiddleware agent-scoped behavior tests
# =============================================================================


class TestToolFilterMiddlewareAgentScope:
    """Tests for agent-scoped middleware behavior."""

    @pytest.mark.asyncio
    async def test_get_agent_id_from_scope_with_valid_id(self):
        """_get_agent_id_from_scope returns UUID when present."""
        agent_id = uuid4()
        mock_request = MagicMock()
        mock_request.scope = {"mcp_agent_id": str(agent_id)}

        with patch(
            "src.services.mcp_server.middleware.get_http_request",
            return_value=mock_request,
        ):
            from src.services.mcp_server.middleware import _get_agent_id_from_scope

            result = _get_agent_id_from_scope()
            assert result == agent_id

    @pytest.mark.asyncio
    async def test_get_agent_id_from_scope_without_id(self):
        """_get_agent_id_from_scope returns None when not present."""
        mock_request = MagicMock()
        mock_request.scope = {}

        with patch(
            "src.services.mcp_server.middleware.get_http_request",
            return_value=mock_request,
        ):
            from src.services.mcp_server.middleware import _get_agent_id_from_scope

            result = _get_agent_id_from_scope()
            assert result is None

    @pytest.mark.asyncio
    async def test_get_agent_id_from_scope_no_request(self):
        """_get_agent_id_from_scope returns None when no request context."""
        with patch(
            "src.services.mcp_server.middleware.get_http_request",
            side_effect=RuntimeError("No request"),
        ):
            from src.services.mcp_server.middleware import _get_agent_id_from_scope

            result = _get_agent_id_from_scope()
            assert result is None
