"""
MCP Tool Filter Middleware

Filters the tools/list response based on the authenticated user's
agent access permissions. This ensures users only see tools from
agents they have access to via their roles.

When an agent_id is present in the ASGI scope (set by AgentScopeMCPMiddleware),
the middleware scopes tools and instructions to that specific agent.
"""

import logging
from uuid import UUID

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)


def _get_agent_id_from_scope() -> UUID | None:
    """Extract agent_id from the ASGI scope if present."""
    try:
        request = get_http_request()
        agent_id_str = request.scope.get("mcp_agent_id")
        if agent_id_str:
            return UUID(agent_id_str)
    except (RuntimeError, ValueError, AttributeError) as e:
        # RuntimeError: get_http_request() raises if not in HTTP context (stdio MCP)
        # ValueError: malformed UUID string in scope
        # AttributeError: scope structure differs
        logger.debug(f"could not extract agent_id from scope: {e}")
    return None


class ToolFilterMiddleware(Middleware):
    """
    FastMCP middleware that filters tools based on user's agent access.

    This middleware intercepts the tools/list MCP method and filters
    the response to only include tools the authenticated user has
    access to based on their role assignments to agents.

    It also blocks execution of tools the user doesn't have access to
    as a second layer of protection.

    When an agent_id is in the ASGI scope:
    - on_initialize: Sets instructions to the agent's system_prompt
    - on_list_tools: Returns only that agent's tools
    - on_call_tool: Enforces access scoped to that agent's tools
    """

    async def on_initialize(self, context: MiddlewareContext, call_next):
        """
        Optionally set instructions from agent's system_prompt.

        If an agent_id is in the ASGI scope, the InitializeResult's
        instructions field is set to the agent's system_prompt.
        """
        result = await call_next(context)

        agent_id = _get_agent_id_from_scope()
        if agent_id is None or result is None:
            return result

        token = get_access_token()
        if token is None:
            return result

        user_roles = token.claims.get("roles", [])
        is_superuser = token.claims.get("is_superuser", False)
        user_id = token.claims.get("user_id")
        org_id = token.claims.get("org_id")

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
                )

            if agent_result and agent_result.system_prompt:
                result = result.model_copy(
                    update={"instructions": agent_result.system_prompt}
                )
                logger.info(
                    f"MCP initialize: Set instructions from agent '{agent_result.agent_name}'"
                )

        except Exception as e:
            logger.exception(f"MCP initialize: Error fetching agent instructions: {e}")

        return result

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

        user_roles = token.claims.get("roles", [])
        is_superuser = token.claims.get("is_superuser", False)
        user_email = token.claims.get("email", "unknown")
        user_id = token.claims.get("user_id")
        org_id = token.claims.get("org_id")

        agent_id = _get_agent_id_from_scope()

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

                if agent_id is not None:
                    # Agent-scoped: only tools from this specific agent
                    agent_result = await service.get_tools_for_agent(
                        agent_id=agent_id,
                        user_roles=user_roles,
                        is_superuser=is_superuser,
                        user_id=user_id,
                        org_id=org_id,
                    )
                    if agent_result is None:
                        logger.warning(
                            f"MCP tools/list: Agent {agent_id} not found or access denied "
                            f"for user {user_email}"
                        )
                        return []
                    accessible_ids = {t.id for t in agent_result.tools}
                else:
                    # All-agents mode (existing behavior)
                    result = await service.get_accessible_tools(
                        user_roles=user_roles,
                        is_superuser=is_superuser,
                        user_id=user_id,
                        org_id=org_id,
                    )
                    accessible_ids = {t.id for t in result.tools}

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

        user_roles = token.claims.get("roles", [])
        is_superuser = token.claims.get("is_superuser", False)
        user_email = token.claims.get("email", "unknown")
        user_id = token.claims.get("user_id")
        org_id = token.claims.get("org_id")

        agent_id = _get_agent_id_from_scope()

        # Check if user has access to this tool
        try:
            from src.core.database import get_db_context
            from src.services.mcp_server.tool_access import MCPToolAccessService

            async with get_db_context() as db:
                service = MCPToolAccessService(db)

                if agent_id is not None:
                    # Agent-scoped: check against this agent's tools only
                    agent_result = await service.get_tools_for_agent(
                        agent_id=agent_id,
                        user_roles=user_roles,
                        is_superuser=is_superuser,
                        user_id=user_id,
                        org_id=org_id,
                    )
                    if agent_result is None:
                        raise ToolError(
                            "Access denied: Agent not found or you don't have permission"
                        )
                    accessible_ids = {t.id for t in agent_result.tools}
                else:
                    # All-agents mode (existing behavior)
                    result = await service.get_accessible_tools(
                        user_roles=user_roles,
                        is_superuser=is_superuser,
                        user_id=user_id,
                        org_id=org_id,
                    )
                    accessible_ids = {t.id for t in result.tools}

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
