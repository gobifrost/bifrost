"""Unit tests for the ``mcp_connections`` connect endpoint flow branching.

Covers the ``client_credentials`` happy path, which the e2e suite cannot
exercise — e2e runs in a separate process and can't patch into the API
container's ``OAuthProviderClient._make_token_request``.

Negative paths (no provider linked, missing token_url, exchange failure)
are also covered here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decrypt_secret, encrypt_secret
from src.models.orm.external_mcp import MCPConnection, MCPServer
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.routers.mcp_connections import (
    MCPConnectActivateResponse,
    _activate_client_credentials,
)


async def _make_org(db: AsyncSession) -> Organization:
    org = Organization(
        id=uuid4(),
        name=f"mcp-cc-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db.add(org)
    await db.flush()
    return org


async def _make_provider(
    db: AsyncSession,
    *,
    flow: str = "client_credentials",
    token_url: str | None = "https://vendor.example.com/oauth/token",
    scopes: list[str] | None = None,
    audience: str | None = None,
) -> OAuthProvider:
    provider = OAuthProvider(
        id=uuid4(),
        provider_name=f"mcp-prov-{uuid4().hex[:8]}",
        oauth_flow_type=flow,
        client_id="__mcp_per_connection__",
        encrypted_client_secret=encrypt_secret("__mcp_per_connection__").encode(),
        token_url=token_url,
        scopes=scopes if scopes is not None else ["read", "write"],
        audience=audience,
    )
    db.add(provider)
    await db.flush()
    return provider


async def _make_server(
    db: AsyncSession, provider: OAuthProvider | None
) -> MCPServer:
    server = MCPServer(
        id=uuid4(),
        name=f"mcp-srv-{uuid4().hex[:8]}",
        server_url="https://vendor.example.com/mcp",
        oauth_provider_id=provider.id if provider else None,
        is_active=True,
    )
    db.add(server)
    await db.flush()
    return server


async def _make_connection(
    db: AsyncSession,
    server: MCPServer,
    org: Organization,
    *,
    client_secret_plaintext: str = "vendor-client-secret-PLAINTEXT",
) -> MCPConnection:
    connection = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="vendor-client-id",
        encrypted_client_secret=encrypt_secret(client_secret_plaintext),
        available_in_chat=False,
        available_to_autonomous=True,
    )
    db.add(connection)
    await db.flush()
    return connection


def _ctx(db: AsyncSession) -> MagicMock:
    """A minimal Context stand-in carrying just the .db attribute the
    helper uses."""
    ctx = MagicMock()
    ctx.db = db
    return ctx


@pytest.mark.asyncio
async def test_activate_client_credentials_happy_path(db_session: AsyncSession):
    """Successful 2-legged exchange persists OAuthToken + sets FK."""
    org = await _make_org(db_session)
    provider = await _make_provider(
        db_session,
        flow="client_credentials",
        scopes=["read", "write"],
        audience="https://vendor.example.com/api",
    )
    server = await _make_server(db_session, provider)
    connection = await _make_connection(
        db_session, server, org, client_secret_plaintext="my-org-secret"
    )

    captured_payload: dict = {}

    async def fake_request(self, token_url, payload):
        captured_payload.update(payload)
        captured_payload["__token_url"] = token_url
        return (
            True,
            {
                "access_token": "vendor-access-token-XYZ",
                "token_type": "Bearer",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
                "scope": "read write",
            },
        )

    with patch(
        "src.services.oauth_provider.OAuthProviderClient._make_token_request",
        new=fake_request,
    ), patch(
        "src.routers.mcp_connections.get_url_resolution_defaults",
        new=AsyncMock(return_value={}),
    ):
        result = await _activate_client_credentials(
            _ctx(db_session), connection, provider
        )

    # Response shape
    assert isinstance(result, MCPConnectActivateResponse)
    assert result.flow == "client_credentials"
    assert result.success is True
    assert result.service_oauth_token_id is not None

    # Payload sent to the vendor must come from the *connection*, not the
    # provider's stored creds (those are placeholders).
    assert captured_payload["grant_type"] == "client_credentials"
    assert captured_payload["client_id"] == "vendor-client-id"
    assert captured_payload["client_secret"] == "my-org-secret"
    assert captured_payload["scope"] == "read write"
    assert captured_payload["audience"] == "https://vendor.example.com/api"
    assert captured_payload["__token_url"] == "https://vendor.example.com/oauth/token"

    # Token row was persisted and linked
    token_row = (
        await db_session.execute(
            select(OAuthToken).where(OAuthToken.id == result.service_oauth_token_id)
        )
    ).scalar_one()
    assert decrypt_secret(token_row.encrypted_access_token.decode()) == (
        "vendor-access-token-XYZ"
    )
    assert token_row.encrypted_refresh_token is None
    assert token_row.provider_id == provider.id
    assert token_row.organization_id == org.id
    assert token_row.user_id is None
    assert token_row.scopes == ["read", "write"]

    # Connection FK updated
    assert connection.service_oauth_token_id == token_row.id


@pytest.mark.asyncio
async def test_activate_client_credentials_reuses_existing_token_row(
    db_session: AsyncSession,
):
    """Re-activating updates the existing token row in place (preserves FK)."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, flow="client_credentials")
    server = await _make_server(db_session, provider)
    connection = await _make_connection(db_session, server, org)

    # Pre-existing service token
    existing_token = OAuthToken(
        id=uuid4(),
        organization_id=org.id,
        provider_id=provider.id,
        encrypted_access_token=encrypt_secret("OLD-TOKEN").encode(),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        scopes=["read"],
    )
    db_session.add(existing_token)
    await db_session.flush()
    connection.service_oauth_token_id = existing_token.id
    await db_session.flush()

    async def fake_request(self, token_url, payload):
        return (
            True,
            {
                "access_token": "ROTATED-TOKEN",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
                "scope": "read",
            },
        )

    with patch(
        "src.services.oauth_provider.OAuthProviderClient._make_token_request",
        new=fake_request,
    ), patch(
        "src.routers.mcp_connections.get_url_resolution_defaults",
        new=AsyncMock(return_value={}),
    ):
        result = await _activate_client_credentials(
            _ctx(db_session), connection, provider
        )

    # Same FK, updated value
    assert result.service_oauth_token_id == existing_token.id
    refreshed = (
        await db_session.execute(
            select(OAuthToken).where(OAuthToken.id == existing_token.id)
        )
    ).scalar_one()
    assert decrypt_secret(refreshed.encrypted_access_token.decode()) == (
        "ROTATED-TOKEN"
    )


@pytest.mark.asyncio
async def test_activate_client_credentials_400_on_exchange_failure(
    db_session: AsyncSession,
):
    """Vendor returns 4xx → 400 with surfaced error message."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, flow="client_credentials")
    server = await _make_server(db_session, provider)
    connection = await _make_connection(db_session, server, org)

    async def fake_request(self, token_url, payload):
        return (
            False,
            {
                "error": "invalid_client",
                "error_description": "Bad credentials",
            },
        )

    with patch(
        "src.services.oauth_provider.OAuthProviderClient._make_token_request",
        new=fake_request,
    ), patch(
        "src.routers.mcp_connections.get_url_resolution_defaults",
        new=AsyncMock(return_value={}),
    ):
        with pytest.raises(HTTPException) as exc:
            await _activate_client_credentials(
                _ctx(db_session), connection, provider
            )

    assert exc.value.status_code == 400
    assert "Bad credentials" in str(exc.value.detail)
    # No token row should have been persisted
    assert connection.service_oauth_token_id is None


@pytest.mark.asyncio
async def test_activate_client_credentials_400_when_token_url_missing(
    db_session: AsyncSession,
):
    """Provider has no token_url → 400."""
    org = await _make_org(db_session)
    provider = await _make_provider(
        db_session, flow="client_credentials", token_url=None
    )
    server = await _make_server(db_session, provider)
    connection = await _make_connection(db_session, server, org)

    with pytest.raises(HTTPException) as exc:
        await _activate_client_credentials(
            _ctx(db_session), connection, provider
        )
    assert exc.value.status_code == 400
    assert "token_url" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_activate_client_credentials_400_when_no_access_token_returned(
    db_session: AsyncSession,
):
    """Vendor returns 200 but no access_token → 400."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, flow="client_credentials")
    server = await _make_server(db_session, provider)
    connection = await _make_connection(db_session, server, org)

    async def fake_request(self, token_url, payload):
        return (True, {"token_type": "Bearer"})

    with patch(
        "src.services.oauth_provider.OAuthProviderClient._make_token_request",
        new=fake_request,
    ), patch(
        "src.routers.mcp_connections.get_url_resolution_defaults",
        new=AsyncMock(return_value={}),
    ):
        with pytest.raises(HTTPException) as exc:
            await _activate_client_credentials(
                _ctx(db_session), connection, provider
            )

    assert exc.value.status_code == 400
    assert "access_token" in str(exc.value.detail).lower()
