"""
MCP System Tools

Each module provides a register_tools(mcp, get_context_fn) function.
"""

from src.services.mcp_server.tools import (
    agents,
    apps,
    builder_workspace,
    claims,
    code_editor,
    configs,
    docs,
    events,
    execution,
    files,
    forms,
    integrations,
    knowledge,
    organizations,
    policy_rules,
    roles,
    sdk,
    skill_assets,
    tables,
    workflow,
)
from src.services.mcp_server.tools import gateway

TOOL_MODULES = [
    agents,
    apps,
    builder_workspace,
    claims,
    code_editor,
    configs,
    docs,
    events,
    execution,
    files,
    forms,
    integrations,
    knowledge,
    organizations,
    policy_rules,
    roles,
    sdk,
    skill_assets,
    tables,
    workflow,
]


def register_all_tools(mcp, get_context_fn) -> None:
    """Register all system tools with FastMCP."""
    for module in TOOL_MODULES:
        module.register_tools(mcp, get_context_fn)


def register_gateway_tools(mcp, get_context_fn) -> None:
    """Register stable discovery/dispatch tools for the unscoped endpoint."""
    gateway.register_tools(mcp, get_context_fn)
