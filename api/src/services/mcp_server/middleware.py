"""Expose the same live gateway on global and agent-scoped MCP endpoints."""

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from src.services.mcp_server.agent_scope import get_scoped_agent_id
from src.services.mcp_server.tools.gateway import GATEWAY_TOOL_NAMES


class ToolFilterMiddleware(Middleware):
    """Keep native tools private and enforce the selected agent's current ACL."""

    async def _authorize_scope(self, *, for_execution: bool) -> None:
        agent_id = get_scoped_agent_id()
        if agent_id is None:
            return
        from shared.scope_resolver import shared_read_organization
        from src.core.database import get_db_context
        from src.services.authorization.enforce import require_named_permission
        from src.services.mcp_server.gateway import MCPAgentGatewayService
        from src.services.mcp_server.server import _get_context_from_token

        context = _get_context_from_token()
        context.scoped_agent_id = agent_id
        service = MCPAgentGatewayService(context)
        try:
            snapshot = await service.get_agent_snapshot(str(agent_id))
            if for_execution:
                async with get_db_context() as db:
                    caller = await service._caller(db)
                    require_named_permission(
                        caller,
                        "agents.execute",
                        shared_read_organization(caller, snapshot.agent.organization_id),
                    )
        except Exception as exc:
            raise ToolError("Agent not found or you do not have permission") from exc

    async def on_list_tools(self, context: MiddlewareContext, call_next) -> list:
        if get_access_token() is None:
            return []
        try:
            await self._authorize_scope(for_execution=False)
        except ToolError:
            return []
        tools = await call_next(context)
        return [tool for tool in tools if tool.name in GATEWAY_TOOL_NAMES]

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        if get_access_token() is None:
            raise ToolError("Authentication required to call tools")
        name = context.message.name
        if name not in GATEWAY_TOOL_NAMES:
            raise ToolError(
                f"Tool '{name}' is not available. Use the Bifrost agent gateway tools."
            )
        await self._authorize_scope(for_execution=True)
        return await call_next(context)
