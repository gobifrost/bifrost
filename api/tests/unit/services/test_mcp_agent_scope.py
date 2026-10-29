"""
Unit tests for agent-scoped MCP endpoint (/mcp/{agent_id}).

Tests:
- AgentScopeMCPMiddleware: ASGI path rewriting
- MCPToolAccessService.get_tools_for_agent: Agent-scoped tool access
- ToolFilterMiddleware: Agent-scoped filtering
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.agent_scope import AgentScopeMCPMiddleware
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller


def _caller(*, home, permissions=(), boundary=None, actor_platform_admin=False) -> Caller:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="mcp-agent-scope@contoso.example",
        organization_id=home,
        is_superuser=actor_platform_admin,
    )
    grants = () if boundary is None else (
        RoleGrant(
            uuid4(),
            frozenset(permissions),
            (Boundary(BoundaryKind.ORGANIZATION, boundary),),
        ),
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=home,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
            role_grants=grants,
        ),
    )


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
    """Tests for Caller-backed agent-scoped tool access."""

    @staticmethod
    def _query() -> MagicMock:
        query = MagicMock()
        query.options.return_value = query
        query.where.return_value = query
        return query

    @pytest.mark.asyncio
    async def test_returns_tools_for_reachable_agent_after_policy_accepts_caller(self):
        from src.models.enums import AgentAccessLevel
        from src.services.mcp_server.tool_access import MCPToolAccessService

        home = uuid4()
        caller = _caller(home=home, permissions={"agents.readbasic"}, boundary=home)
        agent = MagicMock()
        agent.id = uuid4()
        agent.access_level = AgentAccessLevel.AUTHENTICATED
        agent.system_tools = ["bifrost_workflow_execute", "bifrost_workflow_list"]
        agent.tools = []
        agent.knowledge_sources = ["docs", "wiki"]
        agent.is_active = True
        result_row = MagicMock()
        result_row.scalars.return_value.unique.return_value.first.return_value = agent
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_row)
        query = self._query()

        with (
            patch("src.services.mcp_server.tool_access.agent_query_in_reach", return_value=query),
            patch("src.services.mcp_server.tool_access.can_read_agent", new=AsyncMock(return_value=True)) as can_read,
            patch("src.services.mcp_server.tool_access.MCPConfigService") as config_service,
        ):
            config = MagicMock(allowed_tool_ids=None, blocked_tool_ids=None)
            config_service.return_value.get_config = AsyncMock(return_value=config)
            result = await MCPToolAccessService(session).get_tools_for_agent(agent.id, caller)

        assert result is not None
        assert result.accessible_namespaces == ["docs", "wiki"]
        assert {tool.id for tool in result.tools} == {
            "bifrost_workflow_execute", "bifrost_workflow_list", "search_knowledge"
        }
        can_read.assert_awaited_once_with(session, caller, agent)

    @pytest.mark.asyncio
    async def test_actor_admin_flag_cannot_grant_access_without_policy(self):
        from src.models.enums import AgentAccessLevel
        from src.services.mcp_server.tool_access import MCPToolAccessService

        home = uuid4()
        caller = _caller(home=home, actor_platform_admin=True)
        agent = MagicMock(id=uuid4(), access_level=AgentAccessLevel.AUTHENTICATED)
        result_row = MagicMock()
        result_row.scalars.return_value.unique.return_value.first.return_value = agent
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_row)
        query = self._query()

        with (
            patch("src.services.mcp_server.tool_access.agent_query_in_reach", return_value=query),
            patch("src.services.mcp_server.tool_access.can_read_agent", new=AsyncMock(return_value=False)) as can_read,
        ):
            result = await MCPToolAccessService(session).get_tools_for_agent(agent.id, caller)

        assert result is None
        can_read.assert_awaited_once_with(session, caller, agent)


# =============================================================================
# ToolFilterMiddleware agent-scoped behavior tests
# =============================================================================


class TestToolFilterMiddlewareAgentScope:
    """Tests for agent-scoped middleware behavior."""

    @pytest.mark.asyncio
    async def test_get_agent_id_from_scope_with_valid_id(self):
        """_get_agent_id_from_scope returns UUID when present."""
        agent_id = uuid4()
        from src.services.mcp_server import agent_scope

        token = agent_scope._REQUEST_SCOPE.set({"mcp_agent_id": str(agent_id)})
        try:
            from src.services.mcp_server.middleware import _get_agent_id_from_scope

            result = _get_agent_id_from_scope()
            assert result == agent_id
        finally:
            agent_scope._REQUEST_SCOPE.reset(token)

    @pytest.mark.asyncio
    async def test_get_agent_id_from_scope_without_id(self):
        """_get_agent_id_from_scope returns None when not present."""
        from src.services.mcp_server import agent_scope

        token = agent_scope._REQUEST_SCOPE.set({})
        try:
            from src.services.mcp_server.middleware import _get_agent_id_from_scope

            result = _get_agent_id_from_scope()
            assert result is None
        finally:
            agent_scope._REQUEST_SCOPE.reset(token)

    @pytest.mark.asyncio
    async def test_get_agent_id_from_scope_no_request(self):
        """_get_agent_id_from_scope returns None when no request context."""
        with patch(
            "src.services.mcp_server.agent_scope._REQUEST_SCOPE",
            MagicMock(get=MagicMock(side_effect=RuntimeError("No request"))),
        ):
            from src.services.mcp_server.middleware import _get_agent_id_from_scope

            result = _get_agent_id_from_scope()
            assert result is None
