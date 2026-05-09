"""
Knowledge MCP Tools

Tools for searching the Bifrost knowledge base.
"""

import logging
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools.db import get_tool_db

# MCPContext is imported where needed to avoid circular imports

logger = logging.getLogger(__name__)

SEARCH_KNOWLEDGE_NAMESPACE_DENIED_CODE = "search_knowledge_namespace_denied"


async def search_knowledge(
    context: Any,
    query: str,
    namespace: str | None = None,
    limit: int = 5,
) -> ToolResult:
    """Search the knowledge base.

    Args:
        context: MCP context with user permissions
        query: Search query text
        namespace: Optional specific namespace to search (must be accessible)
        limit: Maximum number of results
    """
    from src.repositories.knowledge import KnowledgeRepository
    from src.services.embeddings import get_embedding_client

    logger.info(f"MCP search_knowledge called with query={query}, namespace={namespace}")

    if not query:
        return error_result("query is required")

    # Validate namespace access
    accessible = context.accessible_namespaces
    if not accessible:
        return success_result(
            "No knowledge sources available",
            {
                "results": [],
                "count": 0,
                "message": "No knowledge sources available. No agents with knowledge access configured.",
            },
        )

    if namespace:
        if namespace not in accessible:
            return error_result(
                f"Access denied: namespace '{namespace}' is not accessible.",
                {"code": SEARCH_KNOWLEDGE_NAMESPACE_DENIED_CODE},
            )
        namespaces_to_search = [namespace]
    else:
        namespaces_to_search = accessible

    try:
        async with get_tool_db(context) as db:
            # Generate query embedding
            embedding_client = await get_embedding_client(db)
            query_embedding = await embedding_client.embed_single(query)

            # Search knowledge store
            repo = KnowledgeRepository(
                db, org_id=context.org_id if context.org_id else None, is_superuser=True
            )
            results = await repo.search(
                query_embedding=query_embedding,
                namespace=namespaces_to_search,
                limit=limit,
                fallback=True,
            )

            if not results:
                return success_result(
                    f"No results found for '{query}'",
                    {
                        "results": [],
                        "count": 0,
                        "message": f"No results found for query: '{query}'",
                    },
                )

            result_data = []
            for doc in results:
                result_data.append({
                    "namespace": doc.namespace,
                    "content": doc.content,
                    "score": doc.score,
                })

            display_text = f"Found {len(result_data)} result(s) for '{query}'"
            return success_result(display_text, {"results": result_data, "count": len(result_data)})

    except Exception as e:
        logger.exception(f"Error searching knowledge via MCP: {e}")
        return error_result(f"Error searching knowledge: {str(e)}")


# Tool metadata for registration
TOOLS = [
    ("search_knowledge", "Search Knowledge", "Search the Bifrost knowledge base."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all knowledge tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "search_knowledge": search_knowledge,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
