"""Service-token binding invariants for external MCP connections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import encrypt_secret
from src.models.orm.external_mcp import MCPConnection, MCPServer
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.routers import mcp_connections as mcp_connections_router
from src.routers.mcp_connections import (
    MCPConnectionUpdateRequest,
    _load_service_token_for_connection_or_422,
)
from src.services.mcp_client.auth_resolution import service_token_binding_error


async def _make_org(db: AsyncSession, name: str) -> Organization:
    org = Organization(
        id=uuid4(),
        name=name,
        is_active=True,
        created_by="test@example.com",
    )
    db.add(org)
    await db.flush()
    return org


async def _make_provider(db: AsyncSession, name: str) -> OAuthProvider:
    provider = OAuthProvider(
        id=uuid4(),
        provider_name=name,
        client_id="test-client",
        encrypted_client_secret=encrypt_secret("test-secret").encode(),
        token_url="https://example.invalid/token",
    )
    db.add(provider)
    await db.flush()
    return provider


async def _make_connection(
    db: AsyncSession, org: Organization, provider: OAuthProvider
) -> MCPConnection:
    server = MCPServer(
        id=uuid4(),
        name=f"{org.name}-server-{uuid4().hex[:8]}",
        server_url="https://example.invalid/mcp",
        oauth_provider_id=provider.id,
        is_active=True,
    )
    connection = MCPConnection(
        id=uuid4(),
        server=server,
        organization_id=org.id,
        client_id="test-client",
        encrypted_client_secret="encrypted-test-secret",
    )
    db.add(connection)
    await db.flush()
    return connection


async def _make_token(
    db: AsyncSession,
    org: Organization,
    provider: OAuthProvider,
    *,
    user_id=None,
    expires_at=None,
) -> OAuthToken:
    token = OAuthToken(
        id=uuid4(),
        organization_id=org.id,
        provider_id=provider.id,
        user_id=user_id,
        encrypted_access_token=encrypt_secret("test-access-token").encode(),
        expires_at=expires_at,
    )
    db.add(token)
    await db.flush()
    return token


@pytest.mark.asyncio
async def test_service_token_reference_accepts_matching_service_token(
    db_session: AsyncSession,
):
    contoso = await _make_org(db_session, "Contoso")
    provider = await _make_provider(db_session, "Contoso MCP")
    connection = await _make_connection(db_session, contoso, provider)
    connection.server_url_override = "https://contoso.example.invalid/mcp"
    token = await _make_token(db_session, contoso, provider)

    loaded = await _load_service_token_for_connection_or_422(
        db_session, connection, token.id
    )

    from src.services.mcp_client.client import _resolve_server_url

    assert loaded is token
    assert service_token_binding_error(connection, token) is None
    assert _resolve_server_url(connection) == connection.server_url_override


@pytest.mark.asyncio
async def test_service_token_reference_rejects_missing_foreign_user_and_provider_tokens(
    db_session: AsyncSession, seed_user
):
    contoso = await _make_org(db_session, "Contoso")
    fabrikam = await _make_org(db_session, "Fabrikam")
    provider = await _make_provider(db_session, "Contoso MCP")
    other_provider = await _make_provider(db_session, "Fabrikam MCP")
    connection = await _make_connection(db_session, contoso, provider)
    foreign_token = await _make_token(db_session, fabrikam, provider)
    user_token = await _make_token(
        db_session, contoso, provider, user_id=seed_user.id
    )
    other_provider_token = await _make_token(db_session, contoso, other_provider)

    for token_id in (
        uuid4(),
        foreign_token.id,
        user_token.id,
        other_provider_token.id,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await _load_service_token_for_connection_or_422(
                db_session, connection, token_id
            )
        assert excinfo.value.status_code == 422
        assert excinfo.value.detail == "Service OAuth token is not valid for this connection"


@pytest.mark.asyncio
async def test_invalid_persisted_service_token_fails_before_dispatch_decryption(
    db_session: AsyncSession,
):
    """A stale cross-org FK must not be accepted by runtime token resolution."""
    from src.services.mcp_client.auth_resolution import resolve_token
    from src.services.mcp_client.errors import MisconfigError

    contoso = await _make_org(db_session, "Contoso")
    fabrikam = await _make_org(db_session, "Fabrikam")
    provider = await _make_provider(db_session, "Contoso MCP")
    connection = await _make_connection(db_session, contoso, provider)
    foreign_token = await _make_token(
        db_session,
        fabrikam,
        provider,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    connection.service_oauth_token_id = foreign_token.id
    await db_session.flush()
    await db_session.refresh(connection, attribute_names=["service_oauth_token", "server"])
    connection.available_to_autonomous = True

    with (
        patch(
            "src.services.mcp_client.auth_resolution._decode_access_token"
        ) as decode_access_token,
        patch(
            "src.services.mcp_client.auth_resolution._refresh_token_in_place",
            new=AsyncMock(return_value=True),
        ) as refresh_token,
    ):
        with pytest.raises(MisconfigError) as excinfo:
            await resolve_token(connection, None, db_session)

    assert excinfo.value.connection_id == connection.id
    decode_access_token.assert_not_called()
    refresh_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_allows_explicit_null_service_token_unbind(
    db_session: AsyncSession, monkeypatch
):
    contoso = await _make_org(db_session, "Contoso")
    provider = await _make_provider(db_session, "Contoso MCP")
    connection = await _make_connection(db_session, contoso, provider)
    token = await _make_token(db_session, contoso, provider)
    connection.service_oauth_token_id = token.id
    await db_session.flush()
    # The production loader selects tools before serializing this response.
    # This narrow route test stubs that loader, so preload the same relationship.
    await db_session.refresh(connection, attribute_names=["tools"])

    monkeypatch.setattr(
        mcp_connections_router,
        "_get_shared_connection_or_404",
        AsyncMock(return_value=connection),
    )

    response = await mcp_connections_router.update_mcp_connection(
        connection.id,
        MCPConnectionUpdateRequest(service_oauth_token_id=None),
        SimpleNamespace(db=db_session),
        SimpleNamespace(),
    )

    assert response.service_oauth_token_id is None
    assert connection.service_oauth_token_id is None


@pytest.mark.asyncio
async def test_catalog_sync_rejects_invalid_persisted_service_token_before_decryption(
    db_session: AsyncSession,
):
    from src.services.mcp_client.catalog_sync import _resolve_service_token_for_sync
    from src.services.mcp_client.errors import MisconfigError

    contoso = await _make_org(db_session, "Contoso")
    fabrikam = await _make_org(db_session, "Fabrikam")
    provider = await _make_provider(db_session, "Contoso MCP")
    connection = await _make_connection(db_session, contoso, provider)
    foreign_token = await _make_token(
        db_session,
        fabrikam,
        provider,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    connection.service_oauth_token_id = foreign_token.id
    await db_session.flush()
    await db_session.refresh(connection, attribute_names=["service_oauth_token", "server"])

    with (
        patch(
            "src.services.mcp_client.catalog_sync._decode_access_token"
        ) as decode_access_token,
        patch(
            "src.services.mcp_client.catalog_sync._refresh_token_in_place",
            new=AsyncMock(return_value=True),
        ) as refresh_token,
    ):
        with pytest.raises(MisconfigError) as excinfo:
            await _resolve_service_token_for_sync(connection, db_session)

    assert excinfo.value.connection_id == connection.id
    decode_access_token.assert_not_called()
    refresh_token.assert_not_awaited()
