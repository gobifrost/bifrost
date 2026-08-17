"""Agent-scoped knowledge search backed by the canonical REST operation."""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from src.services.knowledge.search_budget import clamp_knowledge_result_limit
from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest


def _rest_error(status_code: int, body: Any) -> ToolResult:
    detail = body.get("detail") if isinstance(body, dict) else None
    return error_result(
        str(detail) if detail else f"Search knowledge failed: HTTP {status_code}",
        {"status_code": status_code, "body": body},
    )


async def bifrost_search_knowledge(
    context: Any,
    query: str,
    namespace: str | None = None,
    limit: int = 5,
    min_score: float | None = None,
    metadata_filter: dict[str, Any] | None = None,
) -> ToolResult:
    """Search only the knowledge namespaces bound to the selected Agent."""
    if not query:
        return error_result("query is required")
    if bool(getattr(context, "is_external", False)):
        return error_result(
            "Access denied: external users cannot search the knowledge store directly."
        )

    agent_id = getattr(context, "agent_id", None)
    if agent_id is None:
        return error_result("Knowledge search requires an Agent-scoped context.")

    accessible = list(context.accessible_namespaces or [])
    if not accessible:
        return success_result(
            "No knowledge sources available",
            {
                "results": [],
                "count": 0,
                "message": (
                    "No knowledge sources available. No agents with knowledge "
                    "access configured."
                ),
            },
        )
    if namespace is not None and namespace not in accessible:
        return error_result(
            f"Access denied: namespace '{namespace}' is not accessible."
        )

    body: dict[str, Any] = {
        "query": query,
        "namespace": [namespace] if namespace is not None else accessible,
        "limit": clamp_knowledge_result_limit(limit),
        "fallback": True,
        "agent_id": str(agent_id),
    }
    if min_score is not None:
        body["min_score"] = min_score
    if metadata_filter is not None:
        body["metadata_filter"] = metadata_filter
    status_code, response = await call_rest(
        context,
        "POST",
        "/api/knowledge/search",
        json_body=body,
    )
    if status_code != 200 or not isinstance(response, list):
        return _rest_error(status_code, response)
    if not response:
        return success_result(
            f"No results found for '{query}'",
            {
                "results": [],
                "count": 0,
                "message": f"No results found for query: '{query}'",
            },
        )
    return success_result(
        f"Found {len(response)} result(s) for '{query}'",
        {"results": response, "count": len(response)},
    )


TOOLS = [
    (
        "bifrost_search_knowledge",
        "Search Knowledge",
        (
            "Hybrid-search the selected Agent's knowledge sources. Returns at "
            "most 5 deduplicated results; use materially different queries for "
            "follow-up searches."
        ),
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register the canonical knowledge-search tool with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    register_tool_with_context(
        mcp,
        bifrost_search_knowledge,
        "bifrost_search_knowledge",
        TOOLS[0][2],
        get_context_fn,
    )


__all__ = ["bifrost_search_knowledge", "register_tools", "TOOLS"]
