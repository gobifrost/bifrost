"""Unit tests for ``mcp_client.auth_resolution.resolve_token``.

Covers all 5 resolution paths from the spec table. Uses real DB rows via
``db_session`` so the SQLAlchemy relationship graph (especially
``MCPConnection.service_oauth_token`` lazy-joined load) is actually
exercised. Mocks only the OAuth refresh HTTP call — we don't want unit
tests dialing out to vendor token endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.security import encrypt_secret
from src.models.orm.external_mcp import (
    MCPConnection,
    MCPServer,
    UserMCPCredential,
)
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.services.mcp_client.auth_resolution import (
    ResolutionPath,
    resolve_token,
)
from src.services.mcp_client.errors import MisconfigError, NeedsReauthError


def _enc(value: str) -> bytes:
    """Encrypt to the LargeBinary shape OAuthToken expects."""
    return encrypt_secret(value).encode()


async def _make_org(db: AsyncSession) -> Organization:
    org = Organization(
        id=uuid4(),
        name=f"mcp-auth-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db.add(org)
    await db.flush()
    return org


async def _make_provider(db: AsyncSession, *, flow: str = "authorization_code") -> OAuthProvider:
    provider = OAuthProvider(
        id=uuid4(),
        provider_name=f"mcp-prov-{uuid4().hex[:8]}",
        oauth_flow_type=flow,
        client_id="client-id",
        encrypted_client_secret=encrypt_secret("client-secret").encode(),
        token_url="https://example.com/oauth/token",
    )
    db.add(provider)
    await db.flush()
    return provider


async def _make_server(db: AsyncSession) -> MCPServer:
    server = MCPServer(
        id=uuid4(),
        name=f"mcp-srv-{uuid4().hex[:8]}",
        server_url="https://vendor.example.com/mcp",
        is_active=True,
    )
    db.add(server)
    await db.flush()
    return server


async def _make_oauth_token(
    db: AsyncSession,
    provider: OAuthProvider,
    *,
    access_token: str = "vendor-access-token",
    refresh_token: str | None = "vendor-refresh-token",
    expires_at: datetime | None = None,
    user_id=None,
) -> OAuthToken:
    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    token = OAuthToken(
        id=uuid4(),
        provider_id=provider.id,
        user_id=user_id,
        encrypted_access_token=_enc(access_token),
        encrypted_refresh_token=_enc(refresh_token) if refresh_token else None,
        expires_at=expires_at,
        scopes=["read"],
    )
    db.add(token)
    await db.flush()
    return token


async def _make_connection(
    db: AsyncSession,
    server: MCPServer,
    org: Organization,
    *,
    available_in_chat: bool = False,
    available_to_autonomous: bool = False,
    service_oauth_token: OAuthToken | None = None,
) -> MCPConnection:
    connection = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="conn-client-id",
        encrypted_client_secret="encrypted-secret-blob",
        available_in_chat=available_in_chat,
        available_to_autonomous=available_to_autonomous,
        service_oauth_token_id=(
            service_oauth_token.id if service_oauth_token else None
        ),
    )
    db.add(connection)
    await db.flush()
    return connection


async def _reload_connection(db: AsyncSession, connection_id) -> MCPConnection:
    """Re-fetch the connection so eager-loaded relationships are populated."""
    from src.models.orm.external_mcp import MCPServer

    result = await db.execute(
        select(MCPConnection)
        .where(MCPConnection.id == connection_id)
        .options(
            selectinload(MCPConnection.service_oauth_token),
            selectinload(MCPConnection.server).selectinload(
                MCPServer.oauth_provider
            ),
        )
    )
    return result.scalar_one()


# ============================================================================
# Path 1: USER_TOKEN — caller present + per-user credential healthy
# ============================================================================


@pytest.mark.asyncio
async def test_path_1_user_token_when_credential_healthy(
    db_session: AsyncSession, seed_user
):
    """Caller has a per-user credential with a fresh token → USER_TOKEN."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    server = await _make_server(db_session)
    connection = await _make_connection(db_session, server, org)

    user_token = await _make_oauth_token(
        db_session, provider, access_token="user-vendor-token"
    )
    db_session.add(
        UserMCPCredential(
            id=uuid4(),
            user_id=seed_user.id,
            connection_id=connection.id,
            oauth_token_id=user_token.id,
            consent_granted_at=datetime.now(timezone.utc),
            granted_scopes=["read"],
        )
    )
    await db_session.flush()
    connection = await _reload_connection(db_session, connection.id)

    access_token, path = await resolve_token(
        connection, seed_user.id, db_session
    )

    assert path == ResolutionPath.USER_TOKEN
    assert access_token == "user-vendor-token"


# ============================================================================
# Path 2: SERVICE_FALLBACK_CHAT — caller present, no creds, chat flag on
# ============================================================================


@pytest.mark.asyncio
async def test_path_2_service_fallback_chat_when_no_user_credential(
    db_session: AsyncSession, seed_user
):
    """No per-user credential + available_in_chat + healthy svc → SERVICE_FALLBACK_CHAT."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    server = await _make_server(db_session)
    service_token = await _make_oauth_token(
        db_session, provider, access_token="service-vendor-token"
    )
    connection = await _make_connection(
        db_session,
        server,
        org,
        available_in_chat=True,
        service_oauth_token=service_token,
    )
    connection = await _reload_connection(db_session, connection.id)

    access_token, path = await resolve_token(
        connection, seed_user.id, db_session
    )

    assert path == ResolutionPath.SERVICE_FALLBACK_CHAT
    assert access_token == "service-vendor-token"


# ============================================================================
# Path 3: NeedsReauthError — caller present, no creds, no fallback path
# ============================================================================


@pytest.mark.asyncio
async def test_path_3_needs_reauth_when_no_fallback(
    db_session: AsyncSession, seed_user
):
    """Caller present, no user creds, available_in_chat=False → NeedsReauthError."""
    org = await _make_org(db_session)
    server = await _make_server(db_session)
    connection = await _make_connection(
        db_session,
        server,
        org,
        available_in_chat=False,
        available_to_autonomous=True,  # autonomous flag is irrelevant here
    )
    connection = await _reload_connection(db_session, connection.id)

    with pytest.raises(NeedsReauthError) as excinfo:
        await resolve_token(connection, seed_user.id, db_session)

    assert excinfo.value.connection_id == connection.id
    assert str(connection.id) in excinfo.value.reauth_url


@pytest.mark.asyncio
async def test_path_3_needs_reauth_when_chat_flag_on_but_no_service_token(
    db_session: AsyncSession, seed_user
):
    """Chat flag on but no service token configured → still NeedsReauthError."""
    org = await _make_org(db_session)
    server = await _make_server(db_session)
    connection = await _make_connection(
        db_session,
        server,
        org,
        available_in_chat=True,
        service_oauth_token=None,
    )
    connection = await _reload_connection(db_session, connection.id)

    with pytest.raises(NeedsReauthError):
        await resolve_token(connection, seed_user.id, db_session)


# ============================================================================
# Path 4: SERVICE_FALLBACK_AUTONOMOUS — caller=None, autonomous flag on
# ============================================================================


@pytest.mark.asyncio
async def test_path_4_service_fallback_autonomous(db_session: AsyncSession):
    """No caller + available_to_autonomous + healthy svc → SERVICE_FALLBACK_AUTONOMOUS."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, flow="client_credentials")
    server = await _make_server(db_session)
    service_token = await _make_oauth_token(
        db_session, provider, access_token="autonomous-vendor-token"
    )
    connection = await _make_connection(
        db_session,
        server,
        org,
        available_to_autonomous=True,
        service_oauth_token=service_token,
    )
    connection = await _reload_connection(db_session, connection.id)

    access_token, path = await resolve_token(connection, None, db_session)

    assert path == ResolutionPath.SERVICE_FALLBACK_AUTONOMOUS
    assert access_token == "autonomous-vendor-token"


# ============================================================================
# Path 5: MisconfigError — caller=None, autonomous flag off
# ============================================================================


@pytest.mark.asyncio
async def test_path_5_misconfig_when_autonomous_flag_off(
    db_session: AsyncSession,
):
    """No caller + flag off → MisconfigError (planner missed a case)."""
    org = await _make_org(db_session)
    server = await _make_server(db_session)
    connection = await _make_connection(
        db_session,
        server,
        org,
        available_in_chat=True,  # chat flag set; autonomous flag is what matters here
        available_to_autonomous=False,
    )
    connection = await _reload_connection(db_session, connection.id)

    with pytest.raises(MisconfigError) as excinfo:
        await resolve_token(connection, None, db_session)
    assert excinfo.value.connection_id == connection.id


@pytest.mark.asyncio
async def test_path_5_misconfig_when_no_service_token_for_autonomous(
    db_session: AsyncSession,
):
    """Autonomous flag on but no service token → MisconfigError (no token to use)."""
    org = await _make_org(db_session)
    server = await _make_server(db_session)
    connection = await _make_connection(
        db_session,
        server,
        org,
        available_to_autonomous=True,
        service_oauth_token=None,
    )
    connection = await _reload_connection(db_session, connection.id)

    with pytest.raises(MisconfigError):
        await resolve_token(connection, None, db_session)


# ============================================================================
# Refresh fallback: expired user token + refresh fails → falls through to
# service token (Path 2)
# ============================================================================


@pytest.mark.asyncio
async def test_expired_user_token_refresh_fails_falls_back_to_service(
    db_session: AsyncSession, seed_user
):
    """User token expired and refresh fails → falls through to service-token chat fallback."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    server = await _make_server(db_session)

    expired_user_token = await _make_oauth_token(
        db_session,
        provider,
        access_token="expired-user-token",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    service_token = await _make_oauth_token(
        db_session, provider, access_token="svc-token"
    )
    connection = await _make_connection(
        db_session,
        server,
        org,
        available_in_chat=True,
        service_oauth_token=service_token,
    )
    db_session.add(
        UserMCPCredential(
            id=uuid4(),
            user_id=seed_user.id,
            connection_id=connection.id,
            oauth_token_id=expired_user_token.id,
            consent_granted_at=datetime.now(timezone.utc),
            granted_scopes=["read"],
        )
    )
    await db_session.flush()
    connection = await _reload_connection(db_session, connection.id)

    # Force the refresh to fail; auth_resolution should fall through.
    async def _failing_refresh(td):
        return {"success": False, "error": "refresh token revoked"}

    with patch(
        "src.services.mcp_client.auth_resolution.refresh_oauth_token_http",
        side_effect=_failing_refresh,
    ):
        access_token, path = await resolve_token(
            connection, seed_user.id, db_session
        )

    assert path == ResolutionPath.SERVICE_FALLBACK_CHAT
    assert access_token == "svc-token"


@pytest.mark.asyncio
async def test_expired_user_token_refresh_succeeds_returns_user_token(
    db_session: AsyncSession, seed_user
):
    """Expired user token + successful refresh → USER_TOKEN with the new value."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    server = await _make_server(db_session)

    expired_user_token = await _make_oauth_token(
        db_session,
        provider,
        access_token="old-user-token",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    connection = await _make_connection(db_session, server, org)
    db_session.add(
        UserMCPCredential(
            id=uuid4(),
            user_id=seed_user.id,
            connection_id=connection.id,
            oauth_token_id=expired_user_token.id,
            consent_granted_at=datetime.now(timezone.utc),
            granted_scopes=["read"],
        )
    )
    await db_session.flush()
    connection = await _reload_connection(db_session, connection.id)

    # Stub refresh to return a new token.
    new_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    async def _successful_refresh(td):
        return {
            "success": True,
            "access_token": "rotated-user-token",
            "encrypted_access_token": encrypt_secret("rotated-user-token").encode(),
            "expires_at": new_expiry,
            "encrypted_refresh_token": encrypt_secret("new-refresh").encode(),
            "scopes": ["read"],
        }

    with patch(
        "src.services.mcp_client.auth_resolution.refresh_oauth_token_http",
        side_effect=_successful_refresh,
    ):
        access_token, path = await resolve_token(
            connection, seed_user.id, db_session
        )

    assert path == ResolutionPath.USER_TOKEN
    assert access_token == "rotated-user-token"
