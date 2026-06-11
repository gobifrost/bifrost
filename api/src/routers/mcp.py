"""
MCP (Model Context Protocol) Router

Provides external access to Bifrost's MCP server for LLM clients like Claude Desktop.
Uses FastMCP to expose tools via Streamable HTTP transport with Bearer token authentication.

Architecture:
    - FastMCP server is mounted as an ASGI sub-application at /mcp
    - JWT Bearer token authentication using Bifrost's existing auth system
    - Tools are dynamically loaded based on user's agent access permissions
    - Platform admins only (initially) - controlled by system config

Authentication:
    Users authenticate through Bifrost's normal login flow (UI or CLI) and use
    their access token as a Bearer token for MCP requests. The token is validated
    using Bifrost's existing JWT infrastructure (HS256 with shared secret).

Usage:
    # Get access token from Bifrost login
    curl -X POST https://your-bifrost.com/auth/login \
        -d '{"email":"admin@example.com","password":"..."}' \
        -H "Content-Type: application/json"

    # Use token for MCP access (example with test initialize)
    curl -X POST https://your-bifrost.com/api/mcp \
        -H "Authorization: Bearer <access_token>" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'
"""

import logging

from fastapi import APIRouter, HTTPException, status
from starlette.middleware.cors import CORSMiddleware

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.mcp import (
    MCPConfigRequest,
    MCPConfigResponse,
    MCPToolInfo,
    MCPToolsResponse,
)
from src.services.mcp_server.config_service import (
    MCPConfigService,
    invalidate_mcp_config_cache,
)

logger = logging.getLogger(__name__)

# Note: Router uses /api/mcp prefix for REST endpoints (status, config)
# The MCP protocol endpoint is also at /api/mcp (FastMCP handles it)
router = APIRouter(prefix="/api/mcp", tags=["mcp"])


# =============================================================================
# MCP Status Endpoint (for debugging/info)
# =============================================================================


@router.get("/status")
async def mcp_status(
    current_user: CurrentActiveUser,
    db: DbSession,
) -> dict:
    """
    Get MCP server status and available tools for the current user.

    This is a REST endpoint (not MCP protocol) for debugging and discovery.
    Returns information about which tools the user has access to based on
    their agent access permissions.
    """
    from src.services.mcp_server.tool_access import MCPToolAccessService

    # Check MCP config for access control
    config_service = MCPConfigService(db)
    config = await config_service.get_config()

    if not config.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="External MCP access is disabled",
        )

    # Per-user tool access is role-scoped inside MCPToolAccessService — a user
    # with no matching agent roles gets an empty list rather than a 403.
    tool_service = MCPToolAccessService(db)
    result = await tool_service.get_accessible_tools(
        user_roles=current_user.roles,
        is_superuser=current_user.is_superuser,
        user_id=current_user.user_id,
        org_id=current_user.organization_id,
        is_external=current_user.is_external,
    )

    return {
        "status": "available",
        "user_id": str(current_user.user_id),
        "is_platform_admin": current_user.is_superuser,
        "tools_count": len(result.tools),
        "tools": [tool.id for tool in result.tools],
        "accessible_agents_count": len(result.accessible_agent_ids),
        "mcp_endpoint": "/mcp",
        "transport": "streamable-http",
        "auth": "oauth2.1",
    }


# =============================================================================
# MCP ASGI App Mount (FastMCP)
# =============================================================================

# Note: The actual MCP protocol endpoint is mounted separately in main.py
# using FastMCP's http_app() method. This router just provides helper endpoints.

def get_mcp_asgi_app():
    """
    Create the FastMCP ASGI application for mounting.

    This creates a FastMCP server with all system tools and OAuth 2.1 authentication,
    then returns the ASGI app that can be mounted at /api (resulting in /api/mcp endpoint).

    Authentication:
        Uses BifrostAuthProvider which implements OAuth 2.1 with:
        - Discovery endpoints (/.well-known/oauth-*)
        - Authorization code flow with PKCE
        - Dynamic client registration
        - JWT token validation using Bifrost's existing tokens

        Users authenticate through Bifrost's normal login flow via OAuth redirect.
        Only platform admins can access MCP (by default, configurable).

    Returns:
        ASGI application from FastMCP
    """
    from contextlib import asynccontextmanager

    from src.services.mcp_server.server import HAS_FASTMCP

    if not HAS_FASTMCP:
        logger.warning("FastMCP not installed - MCP HTTP endpoint will not be available")
        return None

    # Import here to avoid circular imports and only when FastMCP is available
    from src.services.mcp_server.server import (
        BifrostMCPServer,
        MCPContext,
        _register_workflow_tools,
    )

    # Create OAuth 2.1 auth provider for Bifrost
    try:
        from src.services.mcp_server.auth import create_bifrost_auth_provider
        auth_provider = create_bifrost_auth_provider()
        logger.info("Created Bifrost OAuth 2.1 auth provider for MCP")
    except ImportError as e:
        logger.warning(f"Could not create auth provider: {e}")
        auth_provider = None

    # Create a default context for tool schema generation
    # The actual user context is derived from the validated JWT token
    default_context = MCPContext(
        user_id="00000000-0000-0000-0000-000000000000",
        is_platform_admin=True,  # Shows all tools in schema
    )

    server = BifrostMCPServer(default_context)
    fastmcp_server = server.get_fastmcp_server(auth=auth_provider)

    # Add tool filtering middleware to filter tools/list based on user permissions
    try:
        from src.services.mcp_server.middleware import ToolFilterMiddleware
        fastmcp_server.add_middleware(ToolFilterMiddleware())
        logger.info("Added ToolFilterMiddleware for per-user tool filtering")
    except ImportError as e:
        logger.warning(f"Could not add ToolFilterMiddleware: {e}")

    # Create ASGI app with default path="/mcp" - we mount at root so FastMCP
    # handles /mcp directly without Starlette's trailing slash redirect
    mcp_app = fastmcp_server.http_app(json_response=True, stateless_http=True)

    # Store original lifespan before wrapping
    original_lifespan = getattr(mcp_app, 'lifespan', None)

    # Create combined lifespan that registers workflow tools on startup
    @asynccontextmanager
    async def combined_lifespan(app):
        """Combined lifespan that registers workflow tools and runs FastMCP lifespan."""
        # Register workflow tools on startup
        try:
            count = await _register_workflow_tools(fastmcp_server, default_context)
            logger.info(f"Registered {count} workflow tools during MCP startup")
        except Exception as e:
            logger.warning(f"Failed to register workflow tools: {e}")

        # Run original FastMCP lifespan if present
        if original_lifespan:
            async with original_lifespan(app):
                yield
        else:
            yield

    # Wrap with agent-scoping middleware to handle /mcp/{agent_id} paths
    from src.services.mcp_server.agent_scope import AgentScopeMCPMiddleware
    agent_scoped_app = AgentScopeMCPMiddleware(mcp_app)

    # Wrap with CORS middleware to expose Mcp-Session-Id header
    # Required for browser-based clients like MCP Inspector to read session ID
    # Without this, CORS policy prevents JavaScript from reading the header
    cors_app = CORSMiddleware(
        agent_scoped_app,
        allow_origins=["*"],  # MCP clients can come from anywhere
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )

    # Store combined lifespan on the wrapper for main.py to find
    cors_app.lifespan = combined_lifespan  # type: ignore[attr-defined]

    logger.info("Created FastMCP ASGI application with OAuth 2.1 auth and CORS")

    return cors_app


# =============================================================================
# MCP Configuration Endpoints (Platform Admin Only)
# =============================================================================


@router.get("/config")
async def get_mcp_config(
    current_user: CurrentActiveUser,
    db: DbSession,
) -> MCPConfigResponse:
    """
    Get MCP external access configuration.

    Returns the current configuration for external MCP access,
    including whether it's enabled and what restrictions apply.
    """
    # Only platform admins can view MCP config
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can view MCP configuration"
        )

    service = MCPConfigService(db)
    config = await service.get_config()

    return MCPConfigResponse(
        enabled=config.enabled,
        allowed_tool_ids=config.allowed_tool_ids,
        blocked_tool_ids=config.blocked_tool_ids or [],
        is_configured=config.is_configured,
        configured_at=config.configured_at,
        configured_by=config.configured_by,
    )


@router.put("/config")
async def update_mcp_config(
    current_user: CurrentActiveUser,
    db: DbSession,
    request: MCPConfigRequest,
) -> MCPConfigResponse:
    """
    Update MCP external access configuration.

    Allows platform admins to configure:
    - Whether MCP is enabled
    - Whether platform admin is required
    - Which tools are allowed/blocked
    """
    # Only platform admins can update MCP config
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can update MCP configuration"
        )

    service = MCPConfigService(db)
    config = await service.save_config(
        enabled=request.enabled,
        allowed_tool_ids=request.allowed_tool_ids,
        blocked_tool_ids=request.blocked_tool_ids,
        updated_by=current_user.email,
    )

    # Invalidate cache so auth middleware picks up changes
    invalidate_mcp_config_cache()

    return MCPConfigResponse(
        enabled=config.enabled,
        allowed_tool_ids=config.allowed_tool_ids,
        blocked_tool_ids=config.blocked_tool_ids or [],
        is_configured=config.is_configured,
        configured_at=config.configured_at,
        configured_by=config.configured_by,
    )


@router.delete("/config")
async def delete_mcp_config(
    current_user: CurrentActiveUser,
    db: DbSession,
) -> dict:
    """
    Delete MCP configuration and revert to defaults.

    This removes any custom configuration and reverts to:
    - enabled: True
    - all tools allowed
    """
    # Only platform admins can delete MCP config
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform administrators can delete MCP configuration"
        )

    service = MCPConfigService(db)
    deleted = await service.delete_config()

    # Invalidate cache
    invalidate_mcp_config_cache()

    if deleted:
        return {"message": "MCP configuration deleted, reverted to defaults"}
    else:
        return {"message": "No custom MCP configuration existed"}


@router.get("/tools")
async def list_mcp_tools(
    current_user: CurrentActiveUser,
    db: DbSession,
) -> MCPToolsResponse:
    """
    List all MCP tools available to the current user.

    Returns tools from agents the user can access, filtered by
    global MCP config allowlist/blocklist.
    """
    from src.services.mcp_server.tool_access import MCPToolAccessService

    # Check MCP config for access control
    config_service = MCPConfigService(db)
    config = await config_service.get_config()

    if not config.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="External MCP access is disabled",
        )

    # Per-user tool access is role-scoped inside MCPToolAccessService.
    tool_service = MCPToolAccessService(db)
    result = await tool_service.get_accessible_tools(
        user_roles=current_user.roles,
        is_superuser=current_user.is_superuser,
        user_id=current_user.user_id,
        org_id=current_user.organization_id,
        is_external=current_user.is_external,
    )

    # Convert ToolInfo to MCPToolInfo for response
    tools = [
        MCPToolInfo(
            id=tool.id,
            name=tool.name,
            description=tool.description,
            is_system=(tool.type == "system"),
        )
        for tool in result.tools
    ]

    return MCPToolsResponse(tools=tools)
