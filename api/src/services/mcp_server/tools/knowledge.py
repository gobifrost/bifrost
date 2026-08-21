"""Agent-scoped knowledge search backed by the canonical REST operation."""

from __future__ import annotations

from typing import Any

from fastmcp.tools import ToolResult

from src.services.knowledge.search_budget import clamp_knowledge_result_limit
from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools._http_bridge import call_rest, rest_client


def _rest_error(
    status_code: int, body: Any, *, action: str = "Knowledge request"
) -> ToolResult:
    detail = body.get("detail") if isinstance(body, dict) else None
    return error_result(
        str(detail) if detail else f"{action} failed: HTTP {status_code}",
        {"status_code": status_code, "body": body},
    )


async def _resolve_boundary(
    context: Any,
    scope: str | None,
) -> str | None:
    if scope is None:
        return None
    if scope.lower() == "global":
        return "platform"

    from bifrost.refs import RefResolver

    async with rest_client(context) as http:
        organization_id = await RefResolver(http).resolve("org", scope)
    return f"organization:{organization_id}"


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
        return _rest_error(status_code, response, action="Search Knowledge")
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


async def bifrost_list_knowledge_namespaces(
    context: Any,
    scope: str | None = None,
) -> ToolResult:
    """List Knowledge namespaces visible in one selected boundary."""
    try:
        boundary = await _resolve_boundary(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Knowledge scope: {exc}")
    status_code, response = await call_rest(
        context,
        "GET",
        "/api/knowledge-sources",
        authorization_boundary=boundary,
    )
    if status_code != 200 or not isinstance(response, list):
        return _rest_error(status_code, response, action="List Knowledge namespaces")
    return success_result(
        f"Found {len(response)} Knowledge namespace(s)",
        {"namespaces": response, "count": len(response)},
    )


async def bifrost_list_knowledge_documents(
    context: Any,
    scope: str | None = None,
    namespace: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> ToolResult:
    """List Knowledge documents visible in one selected boundary."""
    try:
        boundary = await _resolve_boundary(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Knowledge scope: {exc}")
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if namespace:
        params["namespace"] = namespace
    if search:
        params["search"] = search
    status_code, response = await call_rest(
        context,
        "GET",
        "/api/knowledge-sources/documents",
        params=params,
        authorization_boundary=boundary,
    )
    if status_code != 200 or not isinstance(response, list):
        return _rest_error(status_code, response, action="List Knowledge documents")
    return success_result(
        f"Found {len(response)} Knowledge document(s)",
        {"documents": response, "count": len(response)},
    )


async def bifrost_get_knowledge_document(
    context: Any,
    namespace: str,
    document_id: str,
    scope: str | None = None,
) -> ToolResult:
    """Get one Knowledge document by namespace and UUID."""
    if not namespace:
        return error_result("namespace is required")
    if not document_id:
        return error_result("document_id is required")
    try:
        boundary = await _resolve_boundary(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Knowledge scope: {exc}")
    status_code, response = await call_rest(
        context,
        "GET",
        f"/api/knowledge-sources/{namespace}/documents/{document_id}",
        authorization_boundary=boundary,
    )
    if status_code != 200 or not isinstance(response, dict):
        return _rest_error(status_code, response, action="Get Knowledge document")
    return success_result(
        f"Knowledge document: {response.get('key') or response.get('id')}",
        response,
    )


async def bifrost_create_knowledge_document(
    context: Any,
    namespace: str,
    content: str,
    key: str | None = None,
    metadata: dict[str, Any] | None = None,
    scope: str | None = None,
) -> ToolResult:
    """Create one Knowledge document in the selected Organization or Global boundary."""
    if not namespace:
        return error_result("namespace is required")
    if not content:
        return error_result("content is required")
    try:
        boundary = await _resolve_boundary(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Knowledge scope: {exc}")
    body: dict[str, Any] = {"content": content, "metadata": metadata or {}}
    if key is not None:
        body["key"] = key
    status_code, response = await call_rest(
        context,
        "POST",
        f"/api/knowledge-sources/{namespace}/documents",
        json_body=body,
        authorization_boundary=boundary,
    )
    if status_code != 201 or not isinstance(response, dict):
        return _rest_error(status_code, response, action="Create Knowledge document")
    return success_result(
        f"Created Knowledge document: {response.get('key') or response.get('id')}",
        response,
    )


async def bifrost_update_knowledge_document(
    context: Any,
    namespace: str,
    document_id: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    scope: str | None = None,
    replace: bool = False,
) -> ToolResult:
    """Update one Knowledge document in the selected exact boundary."""
    if not namespace:
        return error_result("namespace is required")
    if not document_id:
        return error_result("document_id is required")
    if not content:
        return error_result("content is required")
    try:
        boundary = await _resolve_boundary(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Knowledge scope: {exc}")
    params = {"replace": replace}
    body: dict[str, Any] = {"content": content}
    if metadata is not None:
        body["metadata"] = metadata
    status_code, response = await call_rest(
        context,
        "PUT",
        f"/api/knowledge-sources/{namespace}/documents/{document_id}",
        json_body=body,
        params=params,
        authorization_boundary=boundary,
    )
    if status_code != 200 or not isinstance(response, dict):
        return _rest_error(status_code, response, action="Update Knowledge document")
    return success_result(
        f"Updated Knowledge document: {response.get('key') or response.get('id')}",
        response,
    )


async def bifrost_delete_knowledge_document(
    context: Any,
    namespace: str,
    document_id: str,
    scope: str | None = None,
) -> ToolResult:
    """Delete one Knowledge document in the selected exact boundary."""
    if not namespace:
        return error_result("namespace is required")
    if not document_id:
        return error_result("document_id is required")
    try:
        boundary = await _resolve_boundary(context, scope)
    except Exception as exc:
        return error_result(f"Invalid Knowledge scope: {exc}")
    status_code, response = await call_rest(
        context,
        "DELETE",
        f"/api/knowledge-sources/{namespace}/documents/{document_id}",
        authorization_boundary=boundary,
    )
    if status_code != 204:
        return _rest_error(status_code, response, action="Delete Knowledge document")
    return success_result(
        f"Deleted Knowledge document: {document_id}",
        {"deleted": document_id, "namespace": namespace},
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
    (
        "bifrost_list_knowledge_namespaces",
        "List Knowledge Namespaces",
        "List Knowledge namespaces visible in the selected Organization or Global context.",
    ),
    (
        "bifrost_list_knowledge_documents",
        "List Knowledge Documents",
        "List Knowledge documents visible in the selected Organization or Global context.",
    ),
    (
        "bifrost_get_knowledge_document",
        "Get Knowledge Document",
        "Get one Knowledge document by namespace and UUID.",
    ),
    (
        "bifrost_create_knowledge_document",
        "Create Knowledge Document",
        "Create one Knowledge document through the canonical REST API.",
    ),
    (
        "bifrost_update_knowledge_document",
        "Update Knowledge Document",
        "Update one Knowledge document through the canonical REST API.",
    ),
    (
        "bifrost_delete_knowledge_document",
        "Delete Knowledge Document",
        "Delete one Knowledge document through the canonical REST API.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register the canonical knowledge-search tool with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    tool_functions = {
        "bifrost_search_knowledge": bifrost_search_knowledge,
        "bifrost_list_knowledge_namespaces": bifrost_list_knowledge_namespaces,
        "bifrost_list_knowledge_documents": bifrost_list_knowledge_documents,
        "bifrost_get_knowledge_document": bifrost_get_knowledge_document,
        "bifrost_create_knowledge_document": bifrost_create_knowledge_document,
        "bifrost_update_knowledge_document": bifrost_update_knowledge_document,
        "bifrost_delete_knowledge_document": bifrost_delete_knowledge_document,
    }
    for name, _title, description in TOOLS:
        register_tool_with_context(
            mcp,
            tool_functions[name],
            name,
            description,
            get_context_fn,
        )


__all__ = [
    "bifrost_search_knowledge",
    "bifrost_list_knowledge_namespaces",
    "bifrost_list_knowledge_documents",
    "bifrost_get_knowledge_document",
    "bifrost_create_knowledge_document",
    "bifrost_update_knowledge_document",
    "bifrost_delete_knowledge_document",
    "register_tools",
    "TOOLS",
]
