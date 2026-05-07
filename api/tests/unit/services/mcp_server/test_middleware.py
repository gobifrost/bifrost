"""
Unit tests for MCP Tool Filter Middleware.

Tests the ToolFilterMiddleware which filters the tools/list MCP response
based on the authenticated user's agent access permissions.
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
    async def test_filters_to_accessible_tools(
        self, mock_tool, mock_access_token, mock_tool_info
    ):
        """Should filter tools to only those the user has access to."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        # User has access to tool1 and tool3, but not tool2
        all_tools = [
            mock_tool("execute_workflow"),
            mock_tool("list_workflows"),
            mock_tool("search_knowledge"),
        ]

        accessible_tools = [
            mock_tool_info("execute_workflow"),
            mock_tool_info("search_knowledge"),
        ]

        call_next = AsyncMock(return_value=all_tools)
        context = MagicMock()
        token = mock_access_token(roles=["admin"], is_superuser=False)

        mock_service_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.tools = accessible_tools
        mock_service_instance.get_accessible_tools = AsyncMock(return_value=mock_result)

        with patch("src.services.mcp_server.middleware.get_access_token", return_value=token), \
             patch("src.core.database.get_db_context") as mock_db_context, \
             patch("src.services.mcp_server.tool_access.MCPToolAccessService", return_value=mock_service_instance):

            mock_db_context.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db_context.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await middleware.on_list_tools(context, call_next)

        # Should only include execute_workflow and search_knowledge
        assert len(result) == 2
        assert result[0].name == "execute_workflow"
        assert result[1].name == "search_knowledge"

    @pytest.mark.asyncio
    async def test_calls_service_with_user_roles(
        self, mock_tool, mock_access_token, mock_tool_info
    ):
        """Should call MCPToolAccessService with user's roles from token."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        all_tools = [mock_tool("execute_workflow")]
        call_next = AsyncMock(return_value=all_tools)
        context = MagicMock()

        user_roles = ["admin", "developer"]
        token = mock_access_token(roles=user_roles, is_superuser=True)

        mock_service_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.tools = [mock_tool_info("execute_workflow")]
        mock_service_instance.get_accessible_tools = AsyncMock(return_value=mock_result)

        with patch("src.services.mcp_server.middleware.get_access_token", return_value=token), \
             patch("src.core.database.get_db_context") as mock_db_context, \
             patch("src.services.mcp_server.tool_access.MCPToolAccessService", return_value=mock_service_instance):

            mock_db_context.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await middleware.on_list_tools(context, call_next)

        # Verify service was called with the user's roles + identity claims.
        # The service uses ``user_id`` and ``org_id`` to build a
        # WorkflowRepository for per-workflow gating (see
        # MCPToolAccessService._build_workflow_repo). The middleware now
        # forwards these from the JWT, so the call must include them.
        call_kwargs = mock_service_instance.get_accessible_tools.call_args.kwargs
        assert call_kwargs["user_roles"] == user_roles
        assert call_kwargs["is_superuser"] is True
        assert "user_id" in call_kwargs
        assert "org_id" in call_kwargs

    @pytest.mark.asyncio
    async def test_returns_empty_on_service_error(
        self, mock_tool, mock_access_token
    ):
        """Should return empty list if service throws an error."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        all_tools = [mock_tool("execute_workflow")]
        call_next = AsyncMock(return_value=all_tools)
        context = MagicMock()
        token = mock_access_token()

        with patch("src.services.mcp_server.middleware.get_access_token", return_value=token), \
             patch("src.core.database.get_db_context") as mock_db_context:

            mock_db_context.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Database error")
            )

            result = await middleware.on_list_tools(context, call_next)

        assert result == []


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
    async def test_allows_authorized_tool_call(
        self, mock_context, mock_access_token, mock_tool_info
    ):
        """Should allow tool call when user has access."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        mock_context.message.name = "execute_workflow"
        expected_result = {"success": True}
        call_next = AsyncMock(return_value=expected_result)
        token = mock_access_token(roles=["admin"], is_superuser=True)

        mock_service_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.tools = [mock_tool_info("execute_workflow")]
        mock_service_instance.get_accessible_tools = AsyncMock(return_value=mock_result)

        with patch("src.services.mcp_server.middleware.get_access_token", return_value=token), \
             patch("src.core.database.get_db_context") as mock_db_context, \
             patch("src.services.mcp_server.tool_access.MCPToolAccessService", return_value=mock_service_instance):

            mock_db_context.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db_context.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await middleware.on_call_tool(mock_context, call_next)

        assert result == expected_result
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocks_unauthorized_tool_call(
        self, mock_context, mock_access_token, mock_tool_info
    ):
        """Should raise ToolError when user doesn't have access to tool."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        mock_context.message.name = "execute_workflow"
        call_next = AsyncMock()
        token = mock_access_token(roles=["viewer"])

        # User only has access to list_workflows, not execute_workflow
        mock_service_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.tools = [mock_tool_info("list_workflows")]
        mock_service_instance.get_accessible_tools = AsyncMock(return_value=mock_result)

        with patch("src.services.mcp_server.middleware.get_access_token", return_value=token), \
             patch("src.core.database.get_db_context") as mock_db_context, \
             patch("src.services.mcp_server.tool_access.MCPToolAccessService", return_value=mock_service_instance):

            mock_db_context.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_db_context.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(Exception) as exc_info:
                await middleware.on_call_tool(mock_context, call_next)

        assert "Access denied" in str(exc_info.value)
        assert "execute_workflow" in str(exc_info.value)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_error_on_service_failure(
        self, mock_context, mock_access_token
    ):
        """Should raise ToolError if service check fails."""
        from src.services.mcp_server.middleware import ToolFilterMiddleware

        middleware = ToolFilterMiddleware()

        mock_context.message.name = "execute_workflow"
        call_next = AsyncMock()
        token = mock_access_token(is_superuser=True)

        with patch("src.services.mcp_server.middleware.get_access_token", return_value=token), \
             patch("src.core.database.get_db_context") as mock_db_context:

            mock_db_context.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Database error")
            )

            with pytest.raises(Exception) as exc_info:
                await middleware.on_call_tool(mock_context, call_next)

        assert "Authorization check failed" in str(exc_info.value)
        call_next.assert_not_called()
