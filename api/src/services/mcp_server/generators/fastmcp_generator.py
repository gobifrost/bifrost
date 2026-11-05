# api/src/services/mcp_server/generators/fastmcp_generator.py
"""
MCP SDK Tool Registration

Minimal helper for registering tools with context injection.
"""

import inspect
import logging
from typing import Any, Callable, get_args

from mcp.types import CallToolResult

from src.services.mcp_server.tool_result import ToolResult

logger = logging.getLogger(__name__)


def register_tool_with_context(
    mcp: Any,
    func: Callable[..., Any],
    name: str,
    description: str,
    get_context_fn: Callable[[], Any],
) -> None:
    """
    Register a tool with automatic context injection.

    The tool function should have `context` as its first parameter.
    This wrapper removes that parameter from the MCP-visible signature
    and injects the context at runtime from get_context_fn().

    Args:
        mcp: MCP SDK server instance
        func: Tool function with context as first param
        name: Tool name for MCP
        description: Tool description for LLM
        get_context_fn: Function to get context at runtime
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.items())

    # Skip first param (context) for FastMCP's signature
    impl_params = params[1:] if params else []
    aliases = {
        alias: param_name
        for param_name, param in impl_params
        for alias in [
            next(
                (
                    metadata.alias
                    for metadata in get_args(param.annotation)
                    if getattr(metadata, "alias", None)
                ),
                None,
            )
        ]
        if alias is not None
    }

    async def wrapper(**kwargs: Any) -> CallToolResult:
        # The bundled SDK validates JSON-schema aliases but passes those alias
        # keys to this Python wrapper. Restore implementation parameter names
        # before forwarding (for example, protocol ``async`` -> ``async_``).
        for alias, parameter_name in aliases.items():
            if alias in kwargs:
                kwargs[parameter_name] = kwargs.pop(alias)
        ctx = get_context_fn()
        if inspect.isawaitable(ctx):
            ctx = await ctx
        result = await func(ctx, **kwargs)
        if isinstance(result, ToolResult):
            return result.to_call_tool_result()
        return result

    # Set function metadata for the SDK's schema compiler.
    wrapper.__name__ = name
    wrapper.__qualname__ = name
    wrapper.__doc__ = description

    # Build signature without context param
    new_params = [param for _, param in impl_params]
    wrapper.__signature__ = sig.replace(  # type: ignore[attr-defined]
        parameters=new_params,
        return_annotation=CallToolResult,
    )

    # Copy annotations (except context)
    annotations: dict[str, Any] = {}
    for param_name, param in impl_params:
        if param.annotation != inspect.Parameter.empty:
            annotations[param_name] = param.annotation
    annotations["return"] = CallToolResult
    wrapper.__annotations__ = annotations

    # Register with the bundled MCP SDK FastMCP implementation.
    mcp.tool(name=name, description=description)(wrapper)
    logger.debug(f"Registered tool: {name}")
