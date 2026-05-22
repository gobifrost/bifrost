"""External MCP per-org connections router.

Connections are per-org instances of a server template. They carry the
encrypted client_secret, the visibility flags
(``available_in_chat`` / ``available_to_autonomous``), and (after the
``/connect`` flow completes) a ``service_oauth_token_id`` pointing at the
shared service token that backs both flags.

Authorization model:
- Org admin (or platform admin) for CRUD; for v1 we accept any user in
  the connection's org plus platform admins. The frontend gates the page
  to admins; locking down further is a Phase 5 job once the role lattice
  is finalized.
- ``/connect`` requires the same scope plus the ability to initiate
  OAuth on behalf of the org (admin path).
- Per-user routes under ``/api/me/mcp-connections/...`` only require
  that the connection is in the caller's org and that
  ``available_in_chat`` is set on it (otherwise per-user delegation is
  meaningless — the user would never use the resulting token).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal, Union
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import joinedload

from src.config import get_settings
from src.core.auth import Context
from src.core.log_safety import log_safe
from src.core.security import decrypt_secret, encrypt_secret
from src.models.contracts.external_mcp import (
    MCPConnectionPublic,
    MCPConnectionSummary,
    MCPConnectionToolPublic,
    UserMCPCredentialPublic,
)
from src.models.orm.external_mcp import MCPConnection, UserMCPCredential
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.repositories.external_mcp import (
    MCPConnectionRepository,
    MCPServerRepository,
)
from src.services.mcp_client import catalog_sync
from src.services.mcp_client.oauth_state import (
    encode_state,
    generate_pkce_verifier,
    remember_nonce,
)
from src.services.oauth_provider import (
    OAuthProviderClient,
    append_query_params,
    get_url_resolution_defaults,
    resolve_url_template,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp-connections", tags=["MCP Connections"])
me_router = APIRouter(prefix="/api/me/mcp-connections", tags=["MCP Connections"])


# =============================================================================
# Request / response models
# =============================================================================


class MCPConnectionCreateRequest(BaseModel):
    """Router-level create payload — accepts plaintext ``client_secret``.

    The internal ``MCPConnectionCreate`` contract carries the
    *already-encrypted* secret because manifest import/export consumes
    it; the API surface accepts plaintext and encrypts here.
    """

    server_id: UUID = Field(...)
    organization_id: UUID = Field(...)
    client_id: str = Field(..., min_length=1, max_length=512)
    client_secret: str = Field(..., min_length=1)
    server_url_override: str | None = Field(default=None, max_length=2048)
    available_in_chat: bool = Field(default=False)
    available_to_autonomous: bool = Field(default=False)


class MCPConnectionUpdateRequest(BaseModel):
    """Router-level update payload — plaintext ``client_secret`` if rotated."""

    client_id: str | None = Field(default=None, min_length=1, max_length=512)
    client_secret: str | None = Field(default=None, min_length=1)
    server_url_override: str | None = Field(default=None, max_length=2048)
    available_in_chat: bool | None = Field(default=None)
    available_to_autonomous: bool | None = Field(default=None)
    service_oauth_token_id: UUID | None = Field(default=None)


class MCPConnectionRefreshToolsResponse(BaseModel):
    """Counts returned after a tool catalog refresh."""

    model_config = ConfigDict(from_attributes=True)

    total: int = Field(..., description="Total tool rows on the connection after sync")
    enabled: int = Field(..., description="Number that are currently enabled")
    disabled: int = Field(..., description="Number that are currently disabled")


class MCPConnectAuthorizeResponse(BaseModel):
    """Response for ``POST /connect`` when the linked OAuth provider uses
    the ``authorization_code`` flow.

    The frontend opens ``authorization_url`` in a popup; the vendor
    redirects back to ``/api/mcp/oauth/callback`` which completes the
    exchange.
    """

    flow: Literal["authorization_code"] = Field(default="authorization_code")
    authorization_url: str = Field(...)
    state: str = Field(...)


class MCPConnectActivateResponse(BaseModel):
    """Response for ``POST /connect`` when the linked OAuth provider uses
    the ``client_credentials`` flow.

    No popup — the server already exchanged client_id+secret for a token
    and stored it as ``service_oauth_token_id``.
    """

    flow: Literal["client_credentials"] = Field(default="client_credentials")
    success: bool = Field(...)
    service_oauth_token_id: UUID = Field(...)


# Discriminated union return type for the connect endpoints. The OpenAPI
# schema renders this as ``oneOf`` on ``flow``; the frontend branches on
# ``flow`` to decide whether to open a popup or just refetch.
MCPConnectResponse = Union[
    MCPConnectAuthorizeResponse, MCPConnectActivateResponse
]


# =============================================================================
# Helpers
# =============================================================================


def _connection_to_public(connection: MCPConnection) -> MCPConnectionPublic:
    """Convert ``MCPConnection`` ORM to its public response.

    The contract model intentionally omits ``encrypted_client_secret``;
    pydantic's ``from_attributes`` discards it during validation.
    """
    public = MCPConnectionPublic.model_validate(connection)
    public.tools = [MCPConnectionToolPublic.model_validate(t) for t in connection.tools]
    return public


async def _get_connection_or_404(
    ctx: Context, connection_id: UUID
) -> MCPConnection:
    """Resolve a connection and enforce org-scope access.

    Platform admins may fetch any connection. Org users may only fetch
    connections in their own org.
    """
    repo = MCPConnectionRepository(
        session=ctx.db,
        org_id=ctx.org_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_platform_admin,
    )
    connection = await repo.get_connection(connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP connection not found",
        )
    if not ctx.user.is_platform_admin and connection.organization_id != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP connection not found",
        )
    return connection


def _enforce_can_write_org(ctx: Context, organization_id: UUID) -> None:
    """Org write scope check.

    For v1, write access requires either platform admin OR membership in
    the target org. We trust the frontend's admin-gating to keep this
    permissive — Phase 5 will tighten with a role check if the role
    lattice gains an explicit ``can_manage_mcp`` permission.
    """
    if ctx.user.is_platform_admin:
        return
    if ctx.org_id == organization_id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Cannot manage MCP connections outside your organization",
    )


def _build_callback_redirect_uri() -> str:
    """The deterministic callback URL registered with the vendor.

    Spec §3 mandates this be fixed per Bifrost deployment so the admin
    can register exactly one redirect URI in the vendor's OAuth app.
    """
    public_url = get_settings().public_url.rstrip("/")
    return f"{public_url}/api/mcp/oauth/callback"


async def _load_provider_or_400(
    ctx: Context, connection: MCPConnection
) -> OAuthProvider:
    """Resolve the OAuth provider linked to the connection's server template.

    Raises 400 if the template has no provider configured.
    """
    provider_id = connection.server.oauth_provider_id
    if provider_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Server template has no OAuth provider configured",
        )
    result = await ctx.db.execute(
        select(OAuthProvider).where(OAuthProvider.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth provider linked to server template is missing",
        )
    return provider


async def _build_authorization_url(
    ctx: Context,
    connection: MCPConnection,
    provider: OAuthProvider,
    state_token: str,
    code_verifier: str,
    redirect_uri: str,
) -> str:
    """Build the vendor's authorize URL for a connect popup.

    Uses the OAuth provider attached to the server template; we resolve
    URL placeholders ({entity_id}) the same way the integrations
    authorize flow does.
    """
    if not provider.authorization_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth provider missing authorization_url; cannot start flow",
        )

    defaults = await get_url_resolution_defaults(ctx.db, provider)
    resolved = resolve_url_template(provider.authorization_url, defaults=defaults)

    from src.services.mcp_client.oauth_state import pkce_challenge_for

    params = {
        "client_id": connection.client_id,
        "response_type": "code",
        "state": state_token,
        "redirect_uri": redirect_uri,
        "code_challenge": pkce_challenge_for(code_verifier),
        "code_challenge_method": "S256",
    }
    if provider.scopes:
        params["scope"] = " ".join(provider.scopes)
    if provider.audience:
        params["audience"] = provider.audience

    return append_query_params(resolved, params)


async def _activate_client_credentials(
    ctx: Context,
    connection: MCPConnection,
    provider: OAuthProvider,
) -> MCPConnectActivateResponse:
    """Run the ``client_credentials`` token exchange (server-to-server, no user).

    The OAuth provider row holds the schema (token_url, scopes, audience,
    flow_type). The actual ``client_id`` and ``client_secret`` come from
    the *connection* — each org has its own pair registered with the
    vendor — so we override the provider's stored credentials at the
    call site rather than using ``provider.client_id`` /
    ``provider.encrypted_client_secret``.
    """
    if not provider.token_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth provider missing token_url; cannot exchange",
        )

    # Decrypt the per-org client_secret from the connection row.
    try:
        client_secret = decrypt_secret(connection.encrypted_client_secret)
    except Exception:
        logger.exception(
            "failed to decrypt client_secret for connection %s",
            log_safe(str(connection.id)),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stored client_secret could not be decrypted",
        )

    defaults = await get_url_resolution_defaults(ctx.db, provider)
    resolved_token_url = resolve_url_template(
        provider.token_url, defaults=defaults
    )

    oauth_client = OAuthProviderClient()
    scopes = " ".join(provider.scopes) if provider.scopes else ""
    success, result = await oauth_client.get_client_credentials_token(
        token_url=resolved_token_url,
        client_id=connection.client_id,
        client_secret=client_secret,
        scopes=scopes,
        audience=provider.audience,
    )

    if not success:
        # Don't echo upstream raw text — we trust the parsed error fields.
        error_msg = result.get("error_description") or result.get(
            "error", "client_credentials exchange failed"
        )
        logger.warning(
            "client_credentials exchange failed for connection %s: %s",
            log_safe(str(connection.id)),
            log_safe(str(error_msg)),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token exchange failed: {error_msg}",
        )

    access_token = result.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vendor returned no access_token",
        )

    # Persist as a new OAuthToken row, reuse if connection already had one.
    expires_at = result.get("expires_at")
    response_scopes_raw = result.get("scope", "")
    response_scopes = (
        response_scopes_raw.split(" ")
        if isinstance(response_scopes_raw, str) and response_scopes_raw
        else (provider.scopes or [])
    )

    encrypted_access = encrypt_secret(access_token).encode()

    if connection.service_oauth_token_id is not None:
        # Update in place — preserves FK referenced by other consumers.
        existing = await ctx.db.execute(
            select(OAuthToken).where(
                OAuthToken.id == connection.service_oauth_token_id
            )
        )
        token_row = existing.scalar_one_or_none()
    else:
        token_row = None

    if token_row is None:
        token_row = OAuthToken(
            organization_id=connection.organization_id,
            provider_id=provider.id,
            user_id=None,
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=None,
            expires_at=expires_at,
            scopes=response_scopes,
        )
        ctx.db.add(token_row)
        await ctx.db.flush()
        connection.service_oauth_token_id = token_row.id
    else:
        token_row.encrypted_access_token = encrypted_access
        token_row.encrypted_refresh_token = None
        token_row.expires_at = expires_at
        token_row.scopes = response_scopes
        token_row.updated_at = datetime.now(timezone.utc)

    connection.updated_at = datetime.now(timezone.utc)
    await ctx.db.flush()

    logger.info(
        "client_credentials connection %s activated (token=%s)",
        log_safe(str(connection.id)),
        log_safe(str(token_row.id)),
    )
    return MCPConnectActivateResponse(
        flow="client_credentials",
        success=True,
        service_oauth_token_id=token_row.id,
    )


# =============================================================================
# Endpoints — admin CRUD
# =============================================================================


@router.get(
    "",
    response_model=list[MCPConnectionSummary],
    summary="List MCP connections",
)
async def list_mcp_connections(
    ctx: Context,
    server_id: UUID | None = Query(default=None),
    scope: str | None = Query(
        default=None,
        description=(
            "Platform admin filter: omit to see all orgs' connections, or "
            "pass an org UUID to filter to that org. Ignored for non-admins."
        ),
    ),
) -> list[MCPConnectionSummary]:
    """List MCP connections.

    Platform admins see all connections by default; pass ``?scope=<uuid>``
    to filter to a single org. Org users see only their own org's
    connections regardless of the ``scope`` parameter.
    """
    if ctx.user.is_platform_admin:
        # Admin path: show all connections (optionally narrowed by scope/server)
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload, selectinload

        query = select(MCPConnection).options(
            joinedload(MCPConnection.server),
            selectinload(MCPConnection.tools),
            joinedload(MCPConnection.service_oauth_token),
        )
        if server_id is not None:
            query = query.where(MCPConnection.server_id == server_id)
        if scope is not None:
            try:
                scope_org = UUID(scope)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid scope value: {scope}",
                )
            query = query.where(MCPConnection.organization_id == scope_org)
        query = query.order_by(MCPConnection.created_at)
        result = await ctx.db.execute(query)
        connections = list(result.scalars().unique().all())
    else:
        # Org user path: strict org-only via repo
        repo = MCPConnectionRepository(
            session=ctx.db,
            org_id=ctx.org_id,
            user_id=ctx.user.user_id,
            is_superuser=False,
        )
        connections = await repo.list_connections(server_id=server_id)

    return [MCPConnectionSummary.model_validate(c) for c in connections]


@router.post(
    "",
    response_model=MCPConnectionPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create MCP connection",
)
async def create_mcp_connection(
    request: MCPConnectionCreateRequest,
    ctx: Context,
) -> MCPConnectionPublic:
    """Create a per-org connection under a server template.

    Encrypts the client_secret at rest using the same envelope encryption
    as ``oauth_providers.encrypted_client_secret``.
    """
    _enforce_can_write_org(ctx, request.organization_id)

    server_repo = MCPServerRepository(
        session=ctx.db,
        org_id=request.organization_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_platform_admin,
    )
    server = await server_repo.get_server(request.server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server template not found",
        )

    # Org users may only target platform-level (org_id NULL) templates or
    # their own org's templates.
    if not ctx.user.is_platform_admin:
        if server.organization_id is not None and server.organization_id != ctx.org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="MCP server template not found",
            )

    connection = MCPConnection(
        server_id=request.server_id,
        organization_id=request.organization_id,
        client_id=request.client_id,
        encrypted_client_secret=encrypt_secret(request.client_secret),
        server_url_override=request.server_url_override,
        available_in_chat=request.available_in_chat,
        available_to_autonomous=request.available_to_autonomous,
    )
    ctx.db.add(connection)
    await ctx.db.flush()
    # Reload via repo so all eager-load options are honored (server, tools).
    conn_repo = MCPConnectionRepository(
        session=ctx.db,
        org_id=request.organization_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_platform_admin,
    )
    refreshed = await conn_repo.get_connection(connection.id)
    assert refreshed is not None  # we just inserted it
    logger.info(
        f"Created MCP connection {refreshed.id} for org {log_safe(request.organization_id)} "
        f"on server {log_safe(request.server_id)}"
    )
    return _connection_to_public(refreshed)


@router.get(
    "/{connection_id}",
    response_model=MCPConnectionPublic,
    summary="Get MCP connection",
)
async def get_mcp_connection(
    connection_id: UUID,
    ctx: Context,
) -> MCPConnectionPublic:
    """Get a single connection with its tool catalog."""
    connection = await _get_connection_or_404(ctx, connection_id)
    return _connection_to_public(connection)


@router.patch(
    "/{connection_id}",
    response_model=MCPConnectionPublic,
    summary="Update MCP connection",
)
async def update_mcp_connection(
    connection_id: UUID,
    request: MCPConnectionUpdateRequest,
    ctx: Context,
) -> MCPConnectionPublic:
    """Update a connection.

    If ``client_secret`` is supplied it is re-encrypted before persist.
    All other fields are passed through verbatim.
    """
    connection = await _get_connection_or_404(ctx, connection_id)
    _enforce_can_write_org(ctx, connection.organization_id)

    update_fields = request.model_dump(exclude_unset=True)
    plaintext_secret = update_fields.pop("client_secret", None)
    if plaintext_secret is not None:
        connection.encrypted_client_secret = encrypt_secret(plaintext_secret)

    for key, value in update_fields.items():
        setattr(connection, key, value)
    connection.updated_at = datetime.now(timezone.utc)

    await ctx.db.flush()
    refreshed = await _get_connection_or_404(ctx, connection_id)
    logger.info("Updated MCP connection %s", log_safe(str(connection_id)))
    return _connection_to_public(refreshed)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete MCP connection",
    description=(
        "Hard delete. Cascades to tool catalog rows and per-user "
        "credentials per the FK definitions."
    ),
)
async def delete_mcp_connection(
    connection_id: UUID,
    ctx: Context,
) -> None:
    """Delete a connection (cascade)."""
    connection = await _get_connection_or_404(ctx, connection_id)
    _enforce_can_write_org(ctx, connection.organization_id)

    await ctx.db.execute(delete(MCPConnection).where(MCPConnection.id == connection_id))
    await ctx.db.flush()
    logger.info("Deleted MCP connection %s", log_safe(str(connection_id)))


# =============================================================================
# Endpoints — per-tool catalog overrides
# =============================================================================


class MCPToolPatchRequest(BaseModel):
    """Body for ``PATCH /api/mcp-connections/{cid}/tools/{tid}``."""

    enabled: bool | None = Field(
        default=None,
        description=(
            "Admin override. False removes this tool from the catalog "
            "surfaced to agents; True puts it back. Independent from the "
            "auto-disable that catalog sync applies when a tool disappears "
            "from the vendor's tools/list (those carry a "
            "disabled_reason starting with 'Removed from server catalog')."
        ),
    )


@router.patch(
    "/{connection_id}/tools/{tool_id}",
    response_model=MCPConnectionToolPublic,
    summary="Update a single tool in the connection's catalog",
    description=(
        "Toggle ``enabled`` for one tool on a connection. Used by the "
        "Connection edit UI when an admin opts a tool out of the catalog. "
        "Setting enabled=true clears any auto-set ``disabled_reason``; "
        "setting enabled=false records ``Manually disabled by admin`` "
        "so a future catalog sync won't auto-re-enable it."
    ),
)
async def update_mcp_connection_tool(
    connection_id: UUID,
    tool_id: UUID,
    request: MCPToolPatchRequest,
    ctx: Context,
) -> MCPConnectionToolPublic:
    """Update one tool's enabled flag on a connection's catalog."""
    from src.models.orm.external_mcp import MCPConnectionTool

    connection = await _get_connection_or_404(ctx, connection_id)
    _enforce_can_write_org(ctx, connection.organization_id)

    tool_q = await ctx.db.execute(
        select(MCPConnectionTool).where(
            MCPConnectionTool.id == tool_id,
            MCPConnectionTool.connection_id == connection_id,
        )
    )
    tool = tool_q.scalar_one_or_none()
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP connection tool not found",
        )

    if request.enabled is not None and request.enabled != tool.enabled:
        tool.enabled = request.enabled
        if request.enabled:
            # Re-enabling clears the auto-disable marker so a future catalog
            # sync's "still missing from vendor's tools/list" check has clean
            # state to work from.
            tool.disabled_reason = None
        else:
            tool.disabled_reason = "Manually disabled by admin"
        tool.updated_at = datetime.now(timezone.utc)

    await ctx.db.flush()
    await ctx.db.refresh(tool)
    return MCPConnectionToolPublic.model_validate(tool)


# =============================================================================
# Endpoints — refresh tools / connect (admin)
# =============================================================================


@router.post(
    "/{connection_id}/refresh-tools",
    response_model=MCPConnectionRefreshToolsResponse,
    summary="Refresh tool catalog",
    description=(
        "Calls ``tools/list`` on the vendor and upserts ``mcp_connection_tools``. "
        "Tools that disappear are flagged ``enabled=False`` rather than deleted "
        "(see ``catalog_sync`` docstring)."
    ),
)
async def refresh_tools(
    connection_id: UUID,
    ctx: Context,
) -> MCPConnectionRefreshToolsResponse:
    """Refresh the per-connection tool catalog."""
    connection = await _get_connection_or_404(ctx, connection_id)
    _enforce_can_write_org(ctx, connection.organization_id)

    try:
        tools = await catalog_sync.sync_catalog(connection, ctx.db)
    except Exception as exc:
        logger.warning(
            "refresh-tools failed for connection %s: %s",
            log_safe(str(connection_id)),
            log_safe(str(exc)),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Catalog sync failed: {exc}",
        )

    enabled = sum(1 for t in tools if t.enabled)
    return MCPConnectionRefreshToolsResponse(
        total=len(tools),
        enabled=enabled,
        disabled=len(tools) - enabled,
    )


@router.post(
    "/{connection_id}/connect",
    response_model=MCPConnectResponse,
    summary="Initiate or activate the shared service connection",
    description=(
        "Branches on the linked OAuth provider's ``oauth_flow_type``:\n\n"
        "- ``authorization_code`` — returns the vendor's authorize URL plus "
        "the signed ``state`` token. The frontend opens the URL in a popup; "
        "the vendor redirects back to ``/api/mcp/oauth/callback`` which "
        "completes the exchange and writes ``service_oauth_token_id`` on "
        "the connection.\n\n"
        "- ``client_credentials`` — performs a server-to-server token "
        "exchange using the connection's client_id+secret and persists the "
        "resulting access token as ``service_oauth_token_id``. Synchronous, "
        "no popup."
    ),
)
async def connect_service_token(
    connection_id: UUID,
    ctx: Context,
) -> MCPConnectResponse:
    """Begin or complete the service-token flow."""
    connection = await _get_connection_or_404(ctx, connection_id)
    _enforce_can_write_org(ctx, connection.organization_id)

    provider = await _load_provider_or_400(ctx, connection)

    if provider.oauth_flow_type == "client_credentials":
        return await _activate_client_credentials(ctx, connection, provider)

    redirect_uri = _build_callback_redirect_uri()
    code_verifier = generate_pkce_verifier()
    state_token, nonce = encode_state(
        connection_id=connection_id,
        flow_type="service",
        pkce_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )
    await remember_nonce(nonce)

    authorization_url = await _build_authorization_url(
        ctx, connection, provider, state_token, code_verifier, redirect_uri
    )
    return MCPConnectAuthorizeResponse(
        flow="authorization_code",
        authorization_url=authorization_url,
        state=state_token,
    )


# =============================================================================
# Endpoints — per-user (delegated) connect
# =============================================================================


@me_router.get(
    "/{connection_id}/connect",
    response_model=MCPConnectAuthorizeResponse,
    summary="Initiate per-user OAuth flow",
    description=(
        "Per-user delegated connect. Returns the vendor's authorize URL "
        "with state encoded for the *user* flow — the callback writes a "
        "``user_mcp_credentials`` row instead of touching "
        "``mcp_connections.service_oauth_token_id``.\n\n"
        "Per-user delegation is only meaningful for ``authorization_code`` "
        "providers; ``client_credentials`` connections are rejected with "
        "400."
    ),
)
async def connect_user_credential(
    connection_id: UUID,
    ctx: Context,
) -> MCPConnectAuthorizeResponse:
    """Begin a per-user OAuth flow for the caller."""
    connection = await _get_connection_or_404(ctx, connection_id)
    # No write-org enforcement for the per-user path: the user is
    # connecting their own credentials, which they're entitled to do
    # provided they can see the connection.

    provider = await _load_provider_or_400(ctx, connection)
    if provider.oauth_flow_type != "authorization_code":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Per-user delegated connections are only supported for "
                "authorization_code flows"
            ),
        )

    redirect_uri = _build_callback_redirect_uri()
    code_verifier = generate_pkce_verifier()
    state_token, nonce = encode_state(
        connection_id=connection_id,
        flow_type="user",
        pkce_verifier=code_verifier,
        user_id=ctx.user.user_id,
        redirect_uri=redirect_uri,
    )
    await remember_nonce(nonce)

    authorization_url = await _build_authorization_url(
        ctx, connection, provider, state_token, code_verifier, redirect_uri
    )
    return MCPConnectAuthorizeResponse(
        flow="authorization_code",
        authorization_url=authorization_url,
        state=state_token,
    )


# =============================================================================
# Per-user credentials enumeration / disconnect
# =============================================================================


@me_router.get(
    "",
    response_model=list[UserMCPCredentialPublic],
    summary="List the caller's per-user MCP credentials",
    description=(
        "Returns one row per MCP connection the caller has personally "
        "OAuth'd. Includes the OAuth token's expiry so the UI can render "
        "'expires in N days'. Does not return the bearer token itself."
    ),
)
async def list_user_credentials(ctx: Context) -> list[UserMCPCredentialPublic]:
    stmt = (
        select(UserMCPCredential)
        .options(joinedload(UserMCPCredential.oauth_token))
        .where(UserMCPCredential.user_id == ctx.user.user_id)
    )
    rows = (await ctx.db.execute(stmt)).scalars().all()
    return [UserMCPCredentialPublic.model_validate(r) for r in rows]


@me_router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect (forget) the caller's per-user credential",
    description=(
        "Deletes the caller's user_mcp_credentials row for this connection "
        "and the underlying oauth_tokens row. Idempotent — returns 204 "
        "whether or not a credential existed."
    ),
)
async def disconnect_user_credential(connection_id: UUID, ctx: Context) -> None:
    cred_q = (
        select(UserMCPCredential)
        .where(UserMCPCredential.user_id == ctx.user.user_id)
        .where(UserMCPCredential.connection_id == connection_id)
    )
    cred = (await ctx.db.execute(cred_q)).scalar_one_or_none()
    if cred is None:
        return  # idempotent: nothing to do

    token_id = cred.oauth_token_id
    await ctx.db.execute(
        delete(UserMCPCredential).where(UserMCPCredential.id == cred.id)
    )
    # Tokens are CASCADE-deleted from credentials, but we own the lifecycle
    # for the per-user case: drop the token explicitly so it doesn't linger
    # if some future schema change drops the cascade.
    await ctx.db.execute(delete(OAuthToken).where(OAuthToken.id == token_id))
    await ctx.db.commit()
    logger.info(
        "user mcp credential disconnected: user=%s connection=%s",
        log_safe(str(ctx.user.user_id)),
        log_safe(str(connection_id)),
    )
