"""
MCP (Model Context Protocol) Router

Provides external access to Bifrost's MCP server for LLM clients like Claude Desktop.
Uses FastMCP to expose tools via Streamable HTTP transport with Bearer token authentication.

Architecture:
    - FastMCP server is mounted as an ASGI sub-application at /mcp
    - JWT Bearer token authentication using Bifrost's existing auth system
    - /mcp exposes stable agent discovery, dispatch, instruction, and memory tools
    - /mcp/{agent_id} preserves the native agent-scoped tool surface

Authentication:
    Users authenticate through Bifrost's normal login flow (UI or CLI) and use
    their access token as a Bearer token for MCP requests. The token is validated
    using Bifrost's existing JWT infrastructure (HS256 with shared secret).

Usage:
    # Get access token from Bifrost login
    curl -X POST https://your-bifrost.com/auth/login \
        --data-urlencode 'username=admin@example.com' \
        --data-urlencode 'password=...'

    # Use token for MCP access (example with test initialize)
    curl -X POST https://your-bifrost.com/mcp \
        -H "Authorization: Bearer <access_token>" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'
"""

import logging
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from starlette.middleware.cors import CORSMiddleware

from src.config import get_settings
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.mcp import (
    MCPConfigRequest,
    MCPConfigResponse,
    MCPGatewayAgentResponse,
    MCPGatewayExecuteRequest,
    MCPGatewayExecuteResponse,
    MCPGatewayFindAgentsResponse,
    MCPGatewayToolSchemaResponse,
    MCPRunInfoResponse,
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


def _gateway_service(current_user: CurrentActiveUser):
    """Create the canonical gateway service for an authenticated REST caller."""
    from src.services.mcp_server.gateway import MCPAgentGatewayService
    from src.services.mcp_server.server import MCPContext

    return MCPAgentGatewayService(
        MCPContext(
            user_id=current_user.user_id,
            org_id=current_user.organization_id,
            is_platform_admin=current_user.is_superuser,
            is_external=current_user.is_external,
            user_email=current_user.email,
            user_name=current_user.name,
        )
    )


async def _require_mcp_enabled(db: DbSession) -> None:
    """Keep the internal gateway REST surface behind the MCP feature flag."""
    config = await MCPConfigService(db).get_config()
    if not config.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="External MCP access is disabled",
        )


def _raise_gateway_http_error(exc: Exception) -> NoReturn:
    """Map structured gateway failures to REST status codes."""
    from src.services.mcp_server.gateway import GatewayError

    if not isinstance(exc, GatewayError):
        raise exc
    status_code = {
        "INVALID_ARGUMENTS": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "AGENT_NOT_FOUND_OR_FORBIDDEN": status.HTTP_404_NOT_FOUND,
        "TOOL_NOT_FOUND_OR_FORBIDDEN": status.HTTP_404_NOT_FOUND,
        "NEEDS_REAUTH": status.HTTP_409_CONFLICT,
        "TOOL_SCHEMA_INVALID": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "TOOL_EXECUTION_FAILED": status.HTTP_502_BAD_GATEWAY,
    }.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    raise HTTPException(status_code=status_code, detail=exc.as_dict()) from exc


@router.get(
    "/gateway/agents",
    response_model=MCPGatewayFindAgentsResponse,
)
async def find_gateway_agents(
    current_user: CurrentActiveUser,
    db: DbSession,
    query: str | None = None,
    limit: int = Query(default=10, ge=1, le=20),
) -> dict:
    """Find accessible agents for progressive MCP discovery."""
    await _require_mcp_enabled(db)
    return await _gateway_service(current_user).find_agents(
        query=query,
        limit=limit,
    )


@router.get(
    "/gateway/agents/{agent_id}",
    response_model=MCPGatewayAgentResponse,
)
async def get_gateway_agent(
    agent_id: str,
    current_user: CurrentActiveUser,
    db: DbSession,
) -> dict:
    """Load one accessible agent's live capability package."""
    await _require_mcp_enabled(db)
    try:
        return await _gateway_service(current_user).get_agent(agent_id)
    except Exception as exc:
        _raise_gateway_http_error(exc)


@router.get(
    "/gateway/agents/{agent_id}/tools/{tool_ref}",
    response_model=MCPGatewayToolSchemaResponse,
)
async def get_gateway_tool_schema(
    agent_id: str,
    tool_ref: str,
    current_user: CurrentActiveUser,
    db: DbSession,
) -> dict:
    """Load the current schema for an agent-bound tool."""
    await _require_mcp_enabled(db)
    try:
        return await _gateway_service(current_user).get_tool_schema(
            agent_id,
            tool_ref,
        )
    except Exception as exc:
        _raise_gateway_http_error(exc)


@router.post(
    "/gateway/agents/{agent_id}/tools/{tool_ref}/execute",
    response_model=MCPGatewayExecuteResponse,
)
async def execute_gateway_tool(
    agent_id: str,
    tool_ref: str,
    request: MCPGatewayExecuteRequest,
    current_user: CurrentActiveUser,
    db: DbSession,
) -> dict:
    """Re-resolve, validate, and execute an agent-bound tool."""
    await _require_mcp_enabled(db)
    try:
        return await _gateway_service(current_user).execute_agent_tool(
            agent_id,
            tool_ref,
            request.arguments,
        )
    except Exception as exc:
        _raise_gateway_http_error(exc)


# =============================================================================
# MCP Status Endpoint (for debugging/info)
# =============================================================================


@router.get("/run", response_model=MCPRunInfoResponse)
async def mcp_run_info(
    current_user: CurrentActiveUser,
    db: DbSession,
) -> MCPRunInfoResponse:
    """Return install information for the Bifrost Agent plugin."""
    from src.services.mcp_server.run_package import build_setup_prompt, mcp_url

    config = await MCPConfigService(db).get_config()
    return MCPRunInfoResponse(
        enabled=config.enabled,
        mcp_url=mcp_url(get_settings().public_url),
        setup_prompt=build_setup_prompt(),
    )


@router.get(
    "/run/plugin",
    responses={200: {"content": {"application/zip": {}}}},
)
async def download_mcp_run_plugin(
    current_user: CurrentActiveUser,
    db: DbSession,
) -> Response:
    """Download the instance-matched Bifrost Agent package."""
    from shared.version import get_version
    from src.services.mcp_server.run_package import (
        PLUGIN_FILENAME,
        build_bifrost_run_plugin,
    )

    await _require_mcp_enabled(db)
    zip_bytes = build_bifrost_run_plugin(
        get_settings().public_url,
        get_version(),
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{PLUGIN_FILENAME}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/status")
async def mcp_status(
    current_user: CurrentActiveUser,
    db: DbSession,
) -> dict:
    """
    Get MCP server status and available tools for the current user.

    This is a REST endpoint (not MCP protocol) for debugging and discovery.
    Returns the stable gateway tools plus the number of agents the caller
    can discover through them.
    """
    from src.services.mcp_server.tools.gateway import GATEWAY_TOOL_NAMES

    # Check MCP config for access control
    config_service = MCPConfigService(db)
    config = await config_service.get_config()

    if not config.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="External MCP access is disabled",
        )

    gateway = _gateway_service(current_user)
    agent_result = await gateway.find_agents(limit=1)
    gateway_tools = sorted(GATEWAY_TOOL_NAMES)

    return {
        "status": "available",
        "user_id": str(current_user.user_id),
        "is_platform_admin": current_user.is_superuser,
        "tools_count": len(gateway_tools),
        "tools": gateway_tools,
        "accessible_agents_count": agent_result["total_matches"],
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

    This creates a FastMCP server with the stable gateway tools plus the native
    tools retained for agent-scoped URLs, then returns the root-mounted ASGI app.

    Authentication:
        Uses BifrostAuthProvider which implements OAuth 2.1 with:
        - Discovery endpoints (/.well-known/oauth-*)
        - Authorization code flow with PKCE
        - Dynamic client registration
        - JWT token validation using Bifrost's existing tokens

        Users authenticate through Bifrost's normal login flow via OAuth redirect.
        Agent and tool visibility is enforced for each authenticated caller.

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
            count = await _register_workflow_tools(fastmcp_server)
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
    List underlying MCP tools available to the current user.

    This inventory backs platform allow/block configuration. The unscoped
    protocol endpoint itself exposes the stable gateway tools.
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
