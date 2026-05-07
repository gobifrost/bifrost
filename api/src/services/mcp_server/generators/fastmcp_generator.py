# api/src/services/mcp_server/generators/fastmcp_generator.py
"""
FastMCP Tool Registration

Minimal helper for registering tools with context injection.
"""

import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable

from fastmcp.tools import ToolResult

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_tool_with_context(
    mcp: "FastMCP",
    func: Callable[..., Any],
    name: str,
    description: str,
    get_context_fn: Callable[[], Any],
) -> None:
    """
    Register a tool with automatic context injection.

    The tool function should have `context` as its first parameter.
    This wrapper removes that parameter from the FastMCP-visible signature
    and injects the context at runtime from get_context_fn().

    Args:
        mcp: FastMCP server instance
        func: Tool function with context as first param
        name: Tool name for MCP
        description: Tool description for LLM
        get_context_fn: Function to get context at runtime
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.items())

    # Skip first param (context) for FastMCP's signature
    impl_params = params[1:] if params else []

    async def wrapper(**kwargs: Any) -> ToolResult:
        ctx = get_context_fn()
        if inspect.isawaitable(ctx):
            ctx = await ctx
        return await func(ctx, **kwargs)

    # Set function metadata for FastMCP
    wrapper.__name__ = name
    wrapper.__qualname__ = name
    wrapper.__doc__ = description

    # Build signature without context param
    new_params = [param for _, param in impl_params]
    wrapper.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]

    # Copy annotations (except context)
    annotations: dict[str, Any] = {}
    for param_name, param in impl_params:
        if param.annotation != inspect.Parameter.empty:
            annotations[param_name] = param.annotation
    annotations["return"] = ToolResult
    wrapper.__annotations__ = annotations

    # Register with FastMCP
    mcp.tool(name=name, description=description)(wrapper)
    logger.debug(f"Registered tool: {name}")
