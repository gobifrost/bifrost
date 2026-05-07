"""External MCP Server templates router.

Server templates are platform-shareable rows on ``mcp_servers`` (NO secrets,
manifest-friendly). Per-org connections that carry the OAuth client secret
live on ``mcp_connections`` and are managed by ``mcp_connections.py``.

Authorization model:
- List: any authenticated user — platform admins see ALL templates,
  regular users see platform-level (org_id NULL) + their own org's.
- Create / update / delete: platform admin only.
- Discover: platform admin only (admin operation, drives the new-server form).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from src.core.auth import Context, CurrentSuperuser
from src.core.log_safety import log_safe
from src.core.org_filter import resolve_org_filter
from src.core.security import encrypt_secret
from src.models.contracts.external_mcp import (
    MCPConnectionPublic,
    MCPConnectionToolPublic,
    MCPServerCreate,
    MCPServerPublic,
    MCPServerSummary,
    MCPServerUpdate,
)
from src.models.orm.external_mcp import MCPServer
from src.models.orm.oauth import OAuthProvider
from src.repositories.external_mcp import MCPServerRepository
from src.services.mcp_client.discovery import discover_oauth_metadata

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-servers", tags=["MCP Servers"])


# =============================================================================
# Response models
# =============================================================================


class MCPServerDiscoverRequest(BaseModel):
    """Request body for the discovery endpoint."""

    server_url: str = Field(..., min_length=1, max_length=2048)


class MCPServerDiscoverResponse(BaseModel):
    """Response wrapper for the discovery endpoint.

    ``metadata`` is ``None`` when both ``/.well-known`` endpoints failed to
    return usable JSON; the frontend falls back to manual entry.
    """

    metadata: dict[str, Any] | None = Field(default=None)


# =============================================================================
# Helpers
# =============================================================================


def _server_to_public(
    server: MCPServer, oauth_flow_type: str | None = None
) -> MCPServerPublic:
    """Convert an ``MCPServer`` ORM row (with eager-loaded connections) to its
    public response model. Tools nested under each connection use the same
    eager-load that the repo already arranges.

    The OAuth provider's ``oauth_flow_type`` is surfaced when supplied — the
    handlers that pre-loaded the provider pass it through; the rest leave
    it ``None`` and the frontend treats that as "no provider linked".
    """
    connections: list[MCPConnectionPublic] = []
    for conn in server.connections:
        tools = [
            MCPConnectionToolPublic.model_validate(t) for t in conn.tools
        ]
        public = MCPConnectionPublic.model_validate(conn)
        public.tools = tools
        connections.append(public)

    public_server = MCPServerPublic.model_validate(server)
    public_server.connections = connections
    public_server.oauth_flow_type = oauth_flow_type
    return public_server


async def _resolve_flow_type(
    ctx: Context, server: MCPServer
) -> str | None:
    """Look up the linked OAuth provider's flow type, if any."""
    if server.oauth_provider_id is None:
        return None
    result = await ctx.db.execute(
        select(OAuthProvider.oauth_flow_type).where(
            OAuthProvider.id == server.oauth_provider_id
        )
    )
    return result.scalar_one_or_none()


# =============================================================================
# Endpoints — list / detail
# =============================================================================


@router.get(
    "",
    response_model=list[MCPServerSummary],
    summary="List MCP server templates",
    description=(
        "List MCP server templates visible to the caller. Platform admins see "
        "all templates (filterable via ``scope``); org users see platform-level "
        "templates + their own org's."
    ),
)
async def list_mcp_servers(
    ctx: Context,
    scope: str | None = Query(
        default=None,
        description=(
            "Platform-admin filter: omit for all, 'global' for platform-level "
            "only, or an org UUID for that org's templates only. Ignored for "
            "non-admin callers."
        ),
    ),
    active_only: bool = Query(
        default=True,
        description="If true, exclude templates with ``is_active = false``.",
    ),
) -> list[MCPServerSummary]:
    """List MCP server templates."""
    try:
        filter_type, filter_org_id = resolve_org_filter(ctx.user, scope)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    is_admin = ctx.user.is_platform_admin
    repo = MCPServerRepository(
        session=ctx.db,
        org_id=filter_org_id if not is_admin else (filter_org_id or ctx.org_id),
        user_id=ctx.user.user_id,
        is_superuser=is_admin,
    )

    if is_admin:
        servers = await repo.list_all_in_scope(filter_type, active_only=active_only)
    else:
        servers = await repo.list_servers(active_only=active_only)

    return [MCPServerSummary.model_validate(s) for s in servers]


@router.get(
    "/{server_id}",
    response_model=MCPServerPublic,
    summary="Get MCP server template",
    description=(
        "Get a server template with its nested per-org connections and "
        "per-connection tool catalog."
    ),
)
async def get_mcp_server(
    server_id: UUID,
    ctx: Context,
) -> MCPServerPublic:
    """Get a server template with connections + tools."""
    repo = MCPServerRepository(
        session=ctx.db,
        org_id=ctx.org_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_platform_admin,
    )
    server = await repo.get_server(server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server not found",
        )

    # Org-scope check for non-admins: they may see platform-level templates
    # (org_id NULL) and their own org's templates only.
    if not ctx.user.is_platform_admin:
        if server.organization_id is not None and server.organization_id != ctx.org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="MCP server not found",
            )

    flow_type = await _resolve_flow_type(ctx, server)
    return _server_to_public(server, oauth_flow_type=flow_type)


# =============================================================================
# Endpoints — create / update / delete (platform admin only)
# =============================================================================


@router.post(
    "",
    response_model=MCPServerPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create MCP server template (platform admin)",
)
async def create_mcp_server(
    request: MCPServerCreate,
    ctx: Context,
    user: CurrentSuperuser,
) -> MCPServerPublic:
    """Create a new server template. Platform admin only.

    If ``oauth_provider`` is set on the request, this also creates an
    ``OAuthProvider`` row and links it on the new server. The provider
    holds the schema (token_url, flow_type, scopes, audience). The actual
    per-org client_id+secret pairs live on ``mcp_connections`` rows since
    each org registers its own OAuth app with the vendor — the provider's
    ``encrypted_client_secret`` is a non-authoritative placeholder.
    """
    if request.oauth_provider_id is not None and request.oauth_provider is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Provide either ``oauth_provider_id`` (link existing) or "
                "``oauth_provider`` (create inline), not both."
            ),
        )

    oauth_provider_id: UUID | None = request.oauth_provider_id

    if request.oauth_provider is not None:
        op = request.oauth_provider
        if op.oauth_flow_type == "authorization_code" and not op.authorization_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "authorization_url is required when oauth_flow_type "
                    "is 'authorization_code'"
                ),
            )

        # Provider-level credentials are placeholders — the authoritative
        # per-org client_id+secret pair lives on mcp_connections. We still
        # set a non-empty value because the column is NOT NULL; it MUST
        # NOT be used for token requests.
        provider = OAuthProvider(
            provider_name=f"mcp:{request.name}",
            display_name=request.name,
            oauth_flow_type=op.oauth_flow_type,
            client_id="__mcp_per_connection__",
            encrypted_client_secret=encrypt_secret(
                "__mcp_per_connection__"
            ).encode(),
            authorization_url=op.authorization_url,
            token_url=op.token_url,
            audience=op.audience,
            scopes=op.scopes,
            organization_id=request.organization_id,
            created_by=user.email,
        )
        ctx.db.add(provider)
        await ctx.db.flush()
        oauth_provider_id = provider.id
        logger.info(
            "Created OAuth provider %s (%s) for MCP server %s",
            log_safe(provider.provider_name),
            provider.id,
            log_safe(request.name),
        )

    server = MCPServer(
        name=request.name,
        server_url=request.server_url,
        oauth_provider_id=oauth_provider_id,
        redirect_url=request.redirect_url,
        discovery_metadata=request.discovery_metadata,
        organization_id=request.organization_id,
        is_active=request.is_active,
    )
    ctx.db.add(server)
    await ctx.db.flush()
    await ctx.db.refresh(server, ["connections"])

    logger.info(
        "Created MCP server template: %s (%s)",
        log_safe(server.name),
        log_safe(str(server.id)),
    )
    flow_type = await _resolve_flow_type(ctx, server)
    return _server_to_public(server, oauth_flow_type=flow_type)


@router.patch(
    "/{server_id}",
    response_model=MCPServerPublic,
    summary="Update MCP server template (platform admin)",
)
async def update_mcp_server(
    server_id: UUID,
    request: MCPServerUpdate,
    ctx: Context,
    user: CurrentSuperuser,
) -> MCPServerPublic:
    """Update a server template. Platform admin only."""
    repo = MCPServerRepository(
        session=ctx.db,
        org_id=None,
        user_id=user.user_id,
        is_superuser=True,
    )
    server = await repo.get_server(server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server not found",
        )

    update_fields = request.model_dump(exclude_unset=True)
    for key, value in update_fields.items():
        setattr(server, key, value)
    server.updated_at = datetime.now(timezone.utc)

    await ctx.db.flush()
    await ctx.db.refresh(server, ["connections"])

    logger.info(
        "Updated MCP server template: %s (%s)",
        log_safe(server.name),
        log_safe(str(server.id)),
    )
    flow_type = await _resolve_flow_type(ctx, server)
    return _server_to_public(server, oauth_flow_type=flow_type)


@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete MCP server template (platform admin)",
    description=(
        "Soft delete (set ``is_active=False``) by default. Pass "
        "``?hard=true`` to cascade-delete the row, its connections, tools, "
        "and any per-user credentials. Soft delete is preferred — the same "
        "treatment as ``integrations.is_deleted`` — so existing agent tool "
        "bindings don't silently break."
    ),
)
async def delete_mcp_server(
    server_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    hard: bool = Query(default=False, description="Hard-delete via cascade if true."),
) -> None:
    """Soft-delete (default) or hard-delete an MCP server template."""
    repo = MCPServerRepository(
        session=ctx.db,
        org_id=None,
        user_id=user.user_id,
        is_superuser=True,
    )
    server = await repo.get_server(server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server not found",
        )

    if hard:
        await ctx.db.execute(delete(MCPServer).where(MCPServer.id == server_id))
        await ctx.db.flush()
        logger.info(
            "Hard-deleted MCP server template: %s (%s)",
            log_safe(server.name),
            log_safe(str(server_id)),
        )
    else:
        server.is_active = False
        server.updated_at = datetime.now(timezone.utc)
        await ctx.db.flush()
        logger.info(
            "Soft-deleted MCP server template: %s (%s)",
            log_safe(server.name),
            log_safe(str(server_id)),
        )


# =============================================================================
# Endpoints — discover OAuth metadata (platform admin only)
# =============================================================================


@router.post(
    "/discover",
    response_model=MCPServerDiscoverResponse,
    summary="Discover OAuth metadata (platform admin)",
    description=(
        "Fetch ``/.well-known/oauth-authorization-server`` and "
        "``/.well-known/oauth-protected-resource`` from the server's host and "
        "return a merged metadata dict. Used by the new-server form to "
        "auto-fill OAuth fields. Returns ``{metadata: null}`` when neither "
        "endpoint is reachable; the frontend falls back to manual entry."
    ),
)
async def discover_mcp_server(
    request: MCPServerDiscoverRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> MCPServerDiscoverResponse:
    """Discover OAuth metadata for a candidate MCP server URL."""
    metadata = await discover_oauth_metadata(request.server_url)
    return MCPServerDiscoverResponse(metadata=metadata)
