"""
MCP Tool Filter Middleware

Exposes a stable discovery/dispatch gateway on the unscoped endpoint and
filters the agent-scoped endpoint to the selected agent's native tools.

When an agent_id is present in the ASGI scope (set by AgentScopeMCPMiddleware),
the middleware preserves that agent's native tool surface.
"""

import logging

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from src.services.mcp_server.agent_scope import (
    get_scoped_agent_id as _get_agent_id_from_scope,
)
from src.services.mcp_server.tools.gateway import GATEWAY_TOOL_NAMES

logger = logging.getLogger(__name__)


class ToolFilterMiddleware(Middleware):
    """
    FastMCP middleware for gateway and agent-scoped tool surfaces.

    The unscoped ``/mcp`` endpoint exposes only the stable gateway tools.
    The ``/mcp/{agent_id}`` endpoint preserves the native per-agent tool
    surface and its existing authorization checks.

    It also blocks execution of tools the user doesn't have access to
    as a second layer of protection.

    When an agent_id is in the ASGI scope:
    - on_list_tools: Returns only that agent's tools
    - on_call_tool: Enforces access scoped to that agent's tools

    When no agent_id is present:
    - on_list_tools: Returns only the gateway tools
    - on_call_tool: Rejects direct calls to hidden native tools
    """

    async def on_list_tools(
        self, context: MiddlewareContext, call_next
    ) -> list:
        """
        Filter tools/list response based on user permissions.

        Args:
            context: FastMCP middleware context
            call_next: Next handler in the chain

        Returns:
            Filtered list of tools the user can access
        """
        # Get all tools first
        all_tools = await call_next(context)

        # Get authenticated user from token
        token = get_access_token()
        if token is None:
            logger.warning("MCP tools/list: No authenticated user, returning empty list")
            return []

        user_email = token.claims.get("email", "unknown")

        agent_id = _get_agent_id_from_scope()
        if agent_id is None:
            filtered_tools = [
                tool for tool in all_tools if tool.name in GATEWAY_TOOL_NAMES
            ]
            logger.info(
                "MCP tools/list: Unscoped gateway returned %s tools for user %s",
                len(filtered_tools),
                user_email,
            )
            return filtered_tools

        user_roles = token.claims.get("roles", [])
        is_superuser = token.claims.get("is_superuser", False)
        is_external = token.claims.get("is_external", False)
        user_id = token.claims.get("user_id")
        org_id = token.claims.get("org_id")
        logger.info(
            f"MCP tools/list: Filtering for user {user_email}, "
            f"roles={user_roles}, is_superuser={is_superuser}, agent_id={agent_id}"
        )

        # Get accessible tool IDs from service
        try:
            from src.core.database import get_db_context
            from src.services.mcp_server.tool_access import MCPToolAccessService

            async with get_db_context() as db:
                service = MCPToolAccessService(db)

                agent_result = await service.get_tools_for_agent(
                    agent_id=agent_id,
                    user_roles=user_roles,
                    is_superuser=is_superuser,
                    user_id=user_id,
                    org_id=org_id,
                    is_external=is_external,
                )
                if agent_result is None:
                    logger.warning(
                        f"MCP tools/list: Agent {agent_id} not found or access denied "
                        f"for user {user_email}"
                    )
                    return []
                accessible_ids = {t.id for t in agent_result.tools}

            # Filter to only accessible tools
            filtered_tools = [
                tool for tool in all_tools
                if tool.name in accessible_ids
            ]

            logger.info(
                f"MCP tools/list: Filtered {len(all_tools)} -> {len(filtered_tools)} tools "
                f"for user {user_email}"
            )

            return filtered_tools

        except Exception as e:
            logger.exception(f"MCP tools/list: Error filtering tools: {e}")
            # On error, return empty list for security
            return []

    async def on_call_tool(
        self, context: MiddlewareContext, call_next
    ):
        """
        Block execution of tools user doesn't have access to.

        This is a second layer of protection in case someone tries to
        call a tool directly without going through tools/list.

        Args:
            context: FastMCP middleware context
            call_next: Next handler in the chain

        Returns:
            Tool execution result if authorized

        Raises:
            ToolError: If user doesn't have access to the tool
        """
        tool_name = context.message.name

        # Get authenticated user from token
        token = get_access_token()
        if token is None:
            raise ToolError("Authentication required to call tools")

        user_email = token.claims.get("email", "unknown")

        agent_id = _get_agent_id_from_scope()
        if agent_id is None:
            if tool_name not in GATEWAY_TOOL_NAMES:
                logger.warning(
                    "MCP tools/call: Unscoped endpoint denied hidden tool '%s' "
                    "for user %s",
                    tool_name,
                    user_email,
                )
                raise ToolError(
                    f"Tool '{tool_name}' is not available on the unscoped MCP "
                    "endpoint. Use the Bifrost agent gateway tools."
                )
            logger.info(
                "MCP tools/call: Unscoped gateway authorized '%s' for user %s",
                tool_name,
                user_email,
            )
            return await call_next(context)

        user_roles = token.claims.get("roles", [])
        is_superuser = token.claims.get("is_superuser", False)
        is_external = token.claims.get("is_external", False)
        user_id = token.claims.get("user_id")
        org_id = token.claims.get("org_id")

        # Check if user has access to this tool
        try:
            from src.core.database import get_db_context
            from src.services.mcp_server.tool_access import MCPToolAccessService

            async with get_db_context() as db:
                service = MCPToolAccessService(db)

                agent_result = await service.get_tools_for_agent(
                    agent_id=agent_id,
                    user_roles=user_roles,
                    is_superuser=is_superuser,
                    user_id=user_id,
                    org_id=org_id,
                    is_external=is_external,
                )
                if agent_result is None:
                    raise ToolError(
                        "Access denied: Agent not found or you don't have permission"
                    )
                accessible_ids = {t.id for t in agent_result.tools}

            if tool_name not in accessible_ids:
                logger.warning(
                    f"MCP tools/call: Access denied for user {user_email} "
                    f"to tool '{tool_name}'"
                )
                raise ToolError(
                    f"Access denied: You don't have permission to use '{tool_name}'"
                )

            logger.info(
                f"MCP tools/call: User {user_email} authorized to call '{tool_name}'"
            )

        except ToolError:
            raise
        except Exception as e:
            logger.exception(f"MCP tools/call: Error checking access: {e}")
            raise ToolError(f"Authorization check failed: {e}")

        # User is authorized, proceed with tool call
        return await call_next(context)
