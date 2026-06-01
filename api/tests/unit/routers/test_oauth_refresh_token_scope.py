"""Regression tests for OAuth token *scope* on the connections /refresh path.

These are DB-backed (real session) because the bug they guard against is
"which ``organization_id`` gets written onto the token row" — a mock session
swallows exactly that. See ``project_oauth_global_token_restamp`` memory.

Bug: ``refresh_token`` (api/src/routers/oauth_connections.py) resolved and
persisted the token under the *caller's* ``ctx.org_id`` instead of the
*provider's* ``organization_id``. A platform admin in the provider org (Covi)
refreshing the GLOBAL client_credentials connection created/updated a token row
stamped with Covi's org id, not NULL. The SDK read cascade
(``get_org_level_for_provider``) only falls back to ``organization_id IS NULL``,
so every non-Covi org then failed to resolve the token.

The token's scope must follow the provider, not the caller.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.core.auth import ExecutionContext, UserPrincipal
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization


async def _make_org(db_session, name: str) -> Organization:
    """A real organization row so the FK on oauth_tokens.organization_id holds.

    The bug stamps the *caller's* org onto the token; without a real org row
    the failure is an FK violation that masks the assertion we care about.
    """
    org = Organization(name=name, created_by="test")
    db_session.add(org)
    await db_session.flush()
    return org


def _principal(org_id):
    return UserPrincipal(
        user_id=uuid4(),
        email="admin@gobifrost.com",
        organization_id=org_id,
        is_superuser=True,
    )


async def _make_global_cc_provider(db_session, *, connection_name: str) -> OAuthProvider:
    """A global (organization_id IS NULL) client_credentials provider."""
    provider = OAuthProvider(
        provider_name=connection_name,
        display_name=connection_name,
        oauth_flow_type="client_credentials",
        client_id="test-client-id",
        encrypted_client_secret=b"encrypted-secret",
        token_url="https://login.example.com/oauth/token",
        scopes=["scope.default"],
        status="completed",
        organization_id=None,  # global connection
    )
    db_session.add(provider)
    await db_session.flush()
    return provider


@pytest.mark.asyncio
async def test_refresh_of_global_connection_stores_token_as_global(db_session):
    """Refreshing a global connection as a provider-org admin must keep the
    token global (organization_id IS NULL), not stamp it with the caller's org.
    """
    from src.routers.oauth_connections import refresh_token

    connection_name = f"halo_{uuid4().hex[:8]}"
    provider = await _make_global_cc_provider(db_session, connection_name=connection_name)

    # Caller is a platform admin whose home org is a concrete (non-global) org —
    # this is the Covi-admin scenario that caused the re-stamp.
    caller = await _make_org(db_session, f"Provider Org {uuid4().hex[:6]}")
    ctx = ExecutionContext(user=_principal(caller.id), org_id=caller.id, db=db_session)

    outcome = {
        "success": True,
        "access_token": "fresh-access-token",
        "encrypted_access_token": b"enc-access",
        "encrypted_refresh_token": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "scopes": ["scope.default"],
    }

    with patch(
        "src.routers.oauth_connections.refresh_oauth_token_http",
        new=AsyncMock(return_value=outcome),
    ):
        resp = await refresh_token(connection_name, ctx, ctx.user)

    assert resp.success is True
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(OAuthToken).where(OAuthToken.provider_id == provider.id)
        )
    ).scalars().all()

    assert len(rows) == 1, "refresh should produce exactly one org-level token row"
    token = rows[0]
    assert token.organization_id is None, (
        "global connection's token must be stored with organization_id=NULL, "
        f"got {token.organization_id} (the caller's org leaked onto the token)"
    )


@pytest.mark.asyncio
async def test_refresh_updates_existing_global_token_in_place(db_session):
    """A pre-existing global token must be updated in place, not duplicated
    under the caller's org.
    """
    from src.routers.oauth_connections import refresh_token

    connection_name = f"halo_{uuid4().hex[:8]}"
    provider = await _make_global_cc_provider(db_session, connection_name=connection_name)

    existing = OAuthToken(
        organization_id=None,
        provider_id=provider.id,
        user_id=None,
        encrypted_access_token=b"old-access",
        scopes=["scope.default"],
        status="completed",
    )
    db_session.add(existing)
    await db_session.flush()

    caller = await _make_org(db_session, f"Provider Org {uuid4().hex[:6]}")
    ctx = ExecutionContext(user=_principal(caller.id), org_id=caller.id, db=db_session)

    outcome = {
        "success": True,
        "access_token": "fresh-access-token",
        "encrypted_access_token": b"enc-access",
        "encrypted_refresh_token": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "scopes": ["scope.default"],
    }

    with patch(
        "src.routers.oauth_connections.refresh_oauth_token_http",
        new=AsyncMock(return_value=outcome),
    ):
        await refresh_token(connection_name, ctx, ctx.user)

    await db_session.flush()
    rows = (
        await db_session.execute(
            select(OAuthToken).where(OAuthToken.provider_id == provider.id)
        )
    ).scalars().all()

    assert len(rows) == 1, "must update the existing global token, not create a second row"
    assert rows[0].organization_id is None


@pytest.mark.asyncio
async def test_refresh_of_org_specific_connection_stays_org_scoped(db_session):
    """An org-specific connection (provider.organization_id set) must keep its
    token scoped to THAT org — the fix must not collapse every token to global.
    This is the "Covi connecting for itself, like any org" case.
    """
    from src.routers.oauth_connections import refresh_token

    org = await _make_org(db_session, f"Managed Org {uuid4().hex[:6]}")
    connection_name = f"halo_{uuid4().hex[:8]}"
    provider = OAuthProvider(
        provider_name=connection_name,
        display_name=connection_name,
        oauth_flow_type="client_credentials",
        client_id="test-client-id",
        encrypted_client_secret=b"encrypted-secret",
        token_url="https://login.example.com/oauth/token",
        scopes=["scope.default"],
        status="completed",
        organization_id=org.id,  # org-specific connection
    )
    db_session.add(provider)
    await db_session.flush()

    ctx = ExecutionContext(user=_principal(org.id), org_id=org.id, db=db_session)

    outcome = {
        "success": True,
        "access_token": "fresh-access-token",
        "encrypted_access_token": b"enc-access",
        "encrypted_refresh_token": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "scopes": ["scope.default"],
    }

    with patch(
        "src.routers.oauth_connections.refresh_oauth_token_http",
        new=AsyncMock(return_value=outcome),
    ):
        await refresh_token(connection_name, ctx, ctx.user)

    await db_session.flush()
    rows = (
        await db_session.execute(
            select(OAuthToken).where(OAuthToken.provider_id == provider.id)
        )
    ).scalars().all()

    assert len(rows) == 1
    assert rows[0].organization_id == org.id, (
        "org-specific connection's token must stay scoped to its own org"
    )
