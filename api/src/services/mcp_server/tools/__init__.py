"""
MCP System Tools

Modules provide the internal system-tool definitions used by agent execution.
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
    files,
    forms,
    integrations,
    knowledge,
    organizations,
    platform_jobs,
    policy_rules,
    roles,
    sdk,
    tables,
    workflow,
)
from src.services.mcp_server.tools import gateway

TOOL_MODULES = [
    agents,
    apps,
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
    platform_jobs,
    policy_rules,
    roles,
    sdk,
    tables,
    workflow,
]


def register_gateway_tools(mcp, get_context_fn) -> None:
    """Register stable discovery/dispatch tools for both MCP endpoints."""
    gateway.register_tools(mcp, get_context_fn)
