"""
MCP System Tools

Each module provides a register_tools(mcp, get_context_fn) function.
"""

from src.services.mcp_server.tools import (
    agents,
    apps,
    claims,
    code_editor,
    configs,
    docs,
    events,
    execution,
    forms,
    integrations,
    knowledge,
    organizations,
    roles,
    sdk,
    tables,
    workflow,
)

TOOL_MODULES = [
    agents,
    apps,
    claims,
    code_editor,
    configs,
    docs,
    events,
    execution,
    forms,
    integrations,
    knowledge,
    organizations,
    roles,
    sdk,
    tables,
    workflow,
]


def register_all_tools(mcp, get_context_fn) -> None:
    """Register all system tools with FastMCP."""
    for module in TOOL_MODULES:
        module.register_tools(mcp, get_context_fn)
