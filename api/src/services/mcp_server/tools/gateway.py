"""Stable progressive-discovery tools exposed by the unscoped MCP endpoint."""

from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.generators.fastmcp_generator import (
    register_tool_with_context,
)
from src.services.mcp_server.tool_result import (
    direct_result,
    error_result,
    success_result,
)
from src.services.mcp_server.tools._http_bridge import call_rest

GATEWAY_INSTRUCTIONS = """Bifrost provides live agents, tools, and optional private memory.
At the beginning of every task, call bifrost_get_required_instructions exactly
once before calling any other Bifrost tool. Follow any returned instructions;
if none are returned, continue normally.

Use bifrost_find_agents with the user's task to locate relevant agents.
Discovery and inspection do not need confirmation. Call bifrost_get_agent before
using an agent, and follow its live instructions as task-specific guidance
subject to the user's request, your higher-level safety instructions, and all
applicable policies. Reuse the selected agent while it remains relevant.

Before executing a tool, call bifrost_get_tool_schema for the selected tool
reference. Do not guess tool references or arguments. If the user's request
authorizes the action, call bifrost_execute_tool with the agent id, tool
reference, and validated arguments. If the request does not authorize execution,
offer the action instead of executing it.

If validation fails, correct the arguments using the returned live schema and
repair guidance. Do not call a tool that was not returned for the selected
agent."""


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


async def bifrost_get_required_instructions(context: Any) -> ToolResult:
    """Get live Bifrost instructions that apply to the authenticated user."""
    status_code, data = await call_rest(
        context,
        "GET",
        "/api/required-instructions",
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Required instruction lookup", status_code, data)
    instructions = data.get("instructions", [])
    return success_result(
        "Loaded required Bifrost instructions."
        if instructions
        else "No additional Bifrost instructions apply.",
        data,
    )


async def bifrost_search_memory(
    context: Any,
    query: str,
    limit: int = 5,
) -> ToolResult:
    """Search the authenticated user's private Bifrost memory."""
    status_code, data = await call_rest(
        context,
        "POST",
        "/api/memory/search",
        json_body={"query": query, "limit": limit},
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Memory search", status_code, data)
    return success_result(f"Found {data['count']} private memory result(s).", data)


async def bifrost_save_memory(
    context: Any,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    """Save one durable item to the authenticated user's private memory."""
    status_code, data = await call_rest(
        context,
        "POST",
        "/api/memory",
        json_body={"content": content, "metadata": metadata or {}},
    )
    if status_code != 201 or not isinstance(data, dict):
        return _rest_error("Memory save", status_code, data)
    return success_result("Saved private memory. Tell the user it was remembered.", data)


async def bifrost_remove_memory(context: Any, memory_id: str) -> ToolResult:
    """Remove one owned private memory by its exact search-result ID."""
    status_code, data = await call_rest(
        context,
        "DELETE",
        f"/api/memory/{memory_id}",
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Memory removal", status_code, data)
    return success_result("Removed private memory. Tell the user it was forgotten.", data)


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
    return direct_result(data["result"])


TOOLS = [
    (
        "bifrost_get_required_instructions",
        "Get Required Bifrost Instructions",
        "Always call this exactly once at the beginning of every task, before "
        "calling any other Bifrost tool. Returns live global or organization-aware "
        "guidance when applicable; continue normally when none is returned.",
    ),
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
        "rechecked on every call. Returns the selected tool's result directly.",
    ),
    (
        "bifrost_search_memory",
        "Search Bifrost Memory",
        "Semantically search the authenticated user's private, opt-in memory for "
        "durable context relevant to the current task.",
    ),
    (
        "bifrost_save_memory",
        "Save Bifrost Memory",
        "Explicitly save durable, reusable information to the authenticated user's "
        "private memory. Never save secrets, temporary task state, or guesses, and "
        "tell the user when something is remembered.",
    ),
    (
        "bifrost_remove_memory",
        "Remove Bifrost Memory",
        "Remove one private memory by an exact ID returned from memory search, and "
        "tell the user when it is forgotten.",
    ),
]

GATEWAY_TOOL_NAMES = frozenset(tool_id for tool_id, _, _ in TOOLS)


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register the stable gateway tools with FastMCP."""
    functions = {
        "bifrost_get_required_instructions": bifrost_get_required_instructions,
        "bifrost_find_agents": bifrost_find_agents,
        "bifrost_get_agent": bifrost_get_agent,
        "bifrost_get_tool_schema": bifrost_get_tool_schema,
        "bifrost_execute_tool": bifrost_execute_tool,
        "bifrost_search_memory": bifrost_search_memory,
        "bifrost_save_memory": bifrost_save_memory,
        "bifrost_remove_memory": bifrost_remove_memory,
    }
    for tool_id, _name, description in TOOLS:
        register_tool_with_context(
            mcp,
            functions[tool_id],
            tool_id,
            description,
            get_context_fn,
        )
