"""Stable progressive-discovery tools exposed by the unscoped MCP endpoint."""

from typing import Annotated, Any

from fastmcp.tools import ToolResult
from pydantic import Field

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

Use bifrost_search_capabilities with the user's task to search accessible agents
and their tools. Results are bounded: matching_tools is never an implicit full
catalog. Read total_tools, returned_tools, complete, and has_more_matches. When
complete is false—or when no matching tool is returned—assume more tools may
exist and search again with the same agent_id plus a different or narrower
query. Do not conclude that a capability is absent from a partial result.

After selecting an agent, call bifrost_search_capabilities again with agent_id
to load its live instructions. Follow those instructions as task-specific
guidance subject to the user's request, your higher-level safety instructions,
and all applicable policies. Add tool_ref to the same call to load the exact
live input schema before execution. Do not guess tool references or arguments.

If the user's request authorizes the action, call bifrost_execute_tool. Calls
are synchronous by default. Set async=true only for workflow tools when an
immediate execution ID is preferable, then use bifrost_get_execution until it
reaches a terminal state. If the request does not authorize execution, offer
the action instead of executing it.

If validation fails, correct the arguments using the returned live schema and
repair guidance. Do not call a tool that was not returned for the selected
agent."""


def _rest_error(action: str, status_code: int, body: Any) -> ToolResult:
    """Preserve the REST gateway's structured repair guidance."""
    detail = body.get("detail") if isinstance(body, dict) else None
    payload = detail if isinstance(detail, dict) else {"body": body}
    message = payload.get("message") or f"{action} failed: HTTP {status_code}"
    return error_result(message, payload)


async def bifrost_search_capabilities(
    context: Any,
    query: str | None = None,
    agent_id: str | None = None,
    tool_ref: str | None = None,
    limit: int = 10,
) -> ToolResult:
    """Search live agents and tools, or hydrate one selected capability."""
    status_code, data = await call_rest(
        context,
        "POST",
        "/api/mcp/gateway/capabilities/search",
        json_body={
            "query": query,
            "agent_id": agent_id,
            "tool_ref": tool_ref,
            "limit": limit,
        },
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Capability search", status_code, data)
    return success_result(
        f"Returned {data['returned_matches']} capability match(es).",
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


async def bifrost_execute_tool(
    context: Any,
    agent_id: str,
    tool_ref: str,
    arguments: dict[str, Any],
    async_: Annotated[bool, Field(alias="async")] = False,
) -> ToolResult:
    """Validate and execute one live tool through its selected agent."""
    status_code, data = await call_rest(
        context,
        "POST",
        f"/api/mcp/gateway/agents/{agent_id}/tools/{tool_ref}/execute",
        json_body={"arguments": arguments, "async": async_},
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Tool execution", status_code, data)
    if data.get("async") is True:
        return success_result(
            f"Queued execution {data['execution_id']}.",
            data,
        )
    return direct_result(data["result"])


async def bifrost_get_execution(
    context: Any,
    execution_id: str,
    result_path: str = "",
    offset: int = 0,
    limit: int = 20,
) -> ToolResult:
    """Get compact status and a bounded result page for an owned execution."""
    status_code, data = await call_rest(
        context,
        "GET",
        f"/api/mcp/gateway/executions/{execution_id}",
        params={
            "result_path": result_path,
            "offset": offset,
            "limit": limit,
        },
    )
    if status_code != 200 or not isinstance(data, dict):
        return _rest_error("Execution lookup", status_code, data)
    return success_result(
        f"Execution {execution_id} is {data['status']}.",
        data,
    )


TOOLS = [
    (
        "bifrost_get_required_instructions",
        "Get Required Bifrost Instructions",
        "Always call this exactly once at the beginning of every task, before "
        "calling any other Bifrost tool. Returns live global or organization-aware "
        "guidance when applicable; continue normally when none is returned.",
    ),
    (
        "bifrost_search_capabilities",
        "Search Bifrost Capabilities",
        "Search accessible agents and tools through one bounded, progressive "
        "surface. Results explicitly disclose omitted tools. Reuse this tool with "
        "agent_id to load instructions and tool_ref to load one exact schema.",
    ),
    (
        "bifrost_execute_tool",
        "Execute Bifrost Tool",
        "Execute a tool reference through its selected agent after inspecting its "
        "live schema. Arguments are strictly validated and authorization is "
        "rechecked on every call. Sync is the default; async=true immediately "
        "returns an execution ID for workflow-backed tools.",
    ),
    (
        "bifrost_get_execution",
        "Get Bifrost Execution",
        "Get ownership-checked status and a bounded result page for an execution "
        "ID returned by async tool execution.",
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
        "bifrost_search_capabilities": bifrost_search_capabilities,
        "bifrost_execute_tool": bifrost_execute_tool,
        "bifrost_get_execution": bifrost_get_execution,
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
