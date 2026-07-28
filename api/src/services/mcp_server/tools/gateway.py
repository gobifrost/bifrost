"""Stable progressive-discovery tools exposed by the unscoped MCP endpoint."""

from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.generators.fastmcp_generator import (
    register_tool_with_context,
)
from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest

GATEWAY_INSTRUCTIONS = """When the four bifrost_* gateway tools are available,
Bifrost exposes live agent capability packages. For each task:
1. Call bifrost_find_agents with the user's task to locate a relevant agent.
2. Call bifrost_get_agent before using that agent. Follow its live instructions
   as task-specific guidance, subject to the user's request and your higher-level
   safety instructions.
3. Call bifrost_get_tool_schema for the selected tool. Do not guess tool
   references or arguments.
4. Call bifrost_execute_tool with the agent id, tool reference, and arguments.
If validation fails, correct the arguments using the returned live schema.
Do not call a tool that was not returned for the selected agent."""


def _rest_error(action: str, status_code: int, body: Any) -> ToolResult:
    """Preserve the REST gateway's structured repair guidance."""
    detail = body.get("detail") if isinstance(body, dict) else None
    payload = detail if isinstance(detail, dict) else {"body": body}
    message = payload.get("message") or f"{action} failed: HTTP {status_code}"
    return error_result(message, payload)


async def bifrost_find_agents(
    context: Any,
    query: str | None = None,
    limit: int = 10,
) -> ToolResult:
    """Find live Bifrost agents relevant to a task."""
    status_code, data = await call_rest(
        context,
        "GET",
        "/api/mcp/gateway/agents",
        params={"query": query, "limit": limit},
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Agent search", status_code, data)
    return success_result(
        f"Found {data['count']} accessible agent(s).",
        data,
    )


async def bifrost_get_agent(context: Any, agent_id: str) -> ToolResult:
    """Get one accessible agent's live instructions and compact tool catalog."""
    status_code, data = await call_rest(
        context,
        "GET",
        f"/api/mcp/gateway/agents/{agent_id}",
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Agent lookup", status_code, data)
    return success_result(f"Loaded agent '{data['agent']['name']}'.", data)


async def bifrost_get_tool_schema(
    context: Any,
    agent_id: str,
    tool_ref: str,
) -> ToolResult:
    """Get the exact live input schema for one tool returned by get-agent."""
    status_code, data = await call_rest(
        context,
        "GET",
        f"/api/mcp/gateway/agents/{agent_id}/tools/{tool_ref}",
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Tool schema lookup", status_code, data)
    return success_result(f"Loaded schema for '{data['name']}'.", data)


async def bifrost_execute_tool(
    context: Any,
    agent_id: str,
    tool_ref: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Validate and execute one live tool through its selected agent."""
    status_code, data = await call_rest(
        context,
        "POST",
        f"/api/mcp/gateway/agents/{agent_id}/tools/{tool_ref}/execute",
        json_body={"arguments": arguments},
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Tool execution", status_code, data)
    return success_result(
        f"Executed '{data['tool_name']}' through '{data['agent_name']}'.",
        data,
    )


TOOLS = [
    (
        "bifrost_find_agents",
        "Find Bifrost Agents",
        "Search accessible live Bifrost agents for capabilities relevant to a task. "
        "Call this first instead of guessing an agent.",
    ),
    (
        "bifrost_get_agent",
        "Get Bifrost Agent",
        "Load one accessible agent's current instructions and compact tool catalog. "
        "Tool schemas are intentionally omitted; inspect the selected tool next.",
    ),
    (
        "bifrost_get_tool_schema",
        "Get Bifrost Tool Schema",
        "Load the exact live input schema for one tool reference returned by "
        "bifrost_get_agent.",
    ),
    (
        "bifrost_execute_tool",
        "Execute Bifrost Tool",
        "Execute a tool reference through its selected agent after inspecting its "
        "live schema. Arguments are strictly validated and authorization is "
        "rechecked on every call.",
    ),
]

GATEWAY_TOOL_NAMES = frozenset(tool_id for tool_id, _, _ in TOOLS)


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register the stable gateway tools with FastMCP."""
    functions = {
        "bifrost_find_agents": bifrost_find_agents,
        "bifrost_get_agent": bifrost_get_agent,
        "bifrost_get_tool_schema": bifrost_get_tool_schema,
        "bifrost_execute_tool": bifrost_execute_tool,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp,
            functions[tool_id],
            tool_id,
            description,
            get_context_fn,
        )
