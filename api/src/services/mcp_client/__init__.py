"""External MCP Client Package

Bifrost as MCP-client: connects out to external MCP servers (HaloPSA,
Microsoft 365 Copilot Graph, future vendors) and exposes their tools to
its own agents. Symmetric counterpart to ``mcp_server`` which serves
Bifrost's workflows out via MCP.

Public surface:
    from src.services.mcp_client import (
        dispatch,
        catalog_sync,
        discovery,
        auth_resolution,
        client,
        errors,
    )
    from src.services.mcp_client.errors import (
        NeedsReauthError,
        MisconfigError,
        ToolDispatchError,
    )
    from src.services.mcp_client.auth_resolution import (
        ResolutionPath,
        resolve_token,
    )

Transport: Streamable HTTP only. No SSE, no stdio.
"""

from src.services.mcp_client import (
    auth_resolution,
    catalog_sync,
    client,
    discovery,
    dispatch,
    errors,
)
from src.services.mcp_client.auth_resolution import (
    ResolutionPath,
    resolve_token,
)
from src.services.mcp_client.errors import (
    MisconfigError,
    NeedsReauthError,
    ToolDispatchError,
)

__all__ = [
    "auth_resolution",
    "catalog_sync",
    "client",
    "discovery",
    "dispatch",
    "errors",
    "MisconfigError",
    "NeedsReauthError",
    "ResolutionPath",
    "ToolDispatchError",
    "resolve_token",
]
