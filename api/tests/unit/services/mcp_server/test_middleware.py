"""
Unit tests for MCP Tool Filter Middleware.

Tests the ToolFilterMiddleware gateway surface on the unscoped MCP endpoint.
Agent-scoped behavior is covered in test_mcp_agent_scope.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


# ==================== Fixtures ====================


@pytest.fixture
def mock_tool():
    """Create a mock MCP tool."""
    def _create_tool(name: str, description: str = "Test tool"):
        tool = MagicMock()
        tool.name = name
        tool.description = description
        return tool
    return _create_tool


@pytest.fixture
def mock_access_token():
    """Create a mock AccessToken."""
    def _create_token(
        user_id: str = str(uuid4()),
        email: str = "test@example.com",
        is_superuser: bool = False,
        roles: list[str] | None = None,
    ):
        token = MagicMock()
        token.claims = {
            "user_id": user_id,
            "email": email,
            "is_superuser": is_superuser,
            "roles": roles or [],
        }
        return token
    return _create_token


@pytest.fixture
def mock_tool_info():
    """Create a mock ToolInfo from MCPToolAccessService."""
    def _create_tool_info(tool_id: str, name: str | None = None):
        info = MagicMock()
        info.id = tool_id
        info.name = name or tool_id
        return info
    return _create_tool_info


@pytest.fixture
def mock_context():
    """Create a mock MiddlewareContext."""
    context = MagicMock()
    context.message = MagicMock()
    return context


# ==================== on_list_tools Tests ====================


class TestOnListTools:
    """Tests for ToolFilterMiddleware.on_list_tools()."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_auth(self, mock_tool):
        """Should return empty list when user is not authenticated."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        all_tools = [mock_tool("tool1"), mock_tool("tool2")]
        call_next = AsyncMock(return_value=all_tools)
        context = MagicMock()

        with patch("src.services.mcp_server.middleware.get_access_token", return_value=None):
            result = await middleware.on_list_tools(context, call_next)

        assert result == []

    @pytest.mark.asyncio
    async def test_unscoped_returns_only_gateway_tools(
        self, mock_tool, mock_access_token
    ):
        """The default endpoint should hide every native and workflow tool."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware
        from src.services.mcp_server.tools.gateway import GATEWAY_TOOL_NAMES

        middleware = ToolFilterMiddleware()

        all_tools = [
            *[mock_tool(name) for name in GATEWAY_TOOL_NAMES],
            mock_tool("execute_workflow"),
            mock_tool("search_knowledge"),
        ]
        call_next = AsyncMock(return_value=all_tools)
        context = MagicMock()
        token = mock_access_token()

        with (
            patch(
                "src.services.mcp_server.middleware.get_access_token",
                return_value=token,
            ),
            patch(
                "src.services.mcp_server.middleware._get_agent_id_from_scope",
                return_value=None,
            ),
        ):
            result = await middleware.on_list_tools(context, call_next)

        assert {tool.name for tool in result} == GATEWAY_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_unscoped_does_not_query_legacy_access_service(
        self, mock_tool, mock_access_token
    ):
        """Gateway discovery, not middleware aggregation, resolves access."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()
        all_tools = [mock_tool("bifrost_search_capabilities")]
        call_next = AsyncMock(return_value=all_tools)
        context = MagicMock()
        token = mock_access_token()

        with (
            patch(
                "src.services.mcp_server.middleware.get_access_token",
                return_value=token,
            ),
            patch(
                "src.services.mcp_server.middleware._get_agent_id_from_scope",
                return_value=None,
            ),
            patch("src.core.database.get_db_context") as mock_db_context,
        ):
            result = await middleware.on_list_tools(context, call_next)

        assert [tool.name for tool in result] == ["bifrost_search_capabilities"]
        mock_db_context.assert_not_called()


# ==================== on_call_tool Tests ====================


class TestOnCallTool:
    """Tests for ToolFilterMiddleware.on_call_tool()."""

    @pytest.mark.asyncio
    async def test_raises_error_when_no_auth(self, mock_context):
        """Should raise ToolError when user is not authenticated."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        mock_context.message.name = "execute_workflow"
        call_next = AsyncMock()

        with patch("src.services.mcp_server.middleware.get_access_token", return_value=None):
            with pytest.raises(Exception) as exc_info:
                await middleware.on_call_tool(mock_context, call_next)

        assert "Authentication required" in str(exc_info.value)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_gateway_tool_call(
        self, mock_context, mock_access_token
    ):
        """The unscoped endpoint should execute a registered gateway tool."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        mock_context.message.name = "bifrost_execute_tool"
        expected_result = {"success": True}
        call_next = AsyncMock(return_value=expected_result)
        token = mock_access_token()

        with (
            patch(
                "src.services.mcp_server.middleware.get_access_token",
                return_value=token,
            ),
            patch(
                "src.services.mcp_server.middleware._get_agent_id_from_scope",
                return_value=None,
            ),
        ):
            result = await middleware.on_call_tool(mock_context, call_next)

        assert result == expected_result
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_native_tool_call_on_unscoped_endpoint(
        self, mock_context, mock_access_token
    ):
        """A caller cannot bypass discovery by naming a hidden native tool."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        mock_context.message.name = "execute_workflow"
        call_next = AsyncMock()
        token = mock_access_token()

        with (
            patch(
                "src.services.mcp_server.middleware.get_access_token",
                return_value=token,
            ),
            patch(
                "src.services.mcp_server.middleware._get_agent_id_from_scope",
                return_value=None,
            ),
        ):
            with pytest.raises(Exception) as exc_info:
                await middleware.on_call_tool(mock_context, call_next)

        assert "not available on the unscoped MCP endpoint" in str(exc_info.value)
        assert "execute_workflow" in str(exc_info.value)
        call_next.assert_not_called()
