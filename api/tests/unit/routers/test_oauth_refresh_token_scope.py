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
from fastapi import HTTPException

from src.core.auth import ExecutionContext
from src.core.principal import UserPrincipal
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.integrations import Integration
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from shared.builtin_roles import USER_ROLE_ID


async def _make_org(db_session, name: str) -> Organization:
    """A real organization row so the FK on oauth_tokens.organization_id holds.

    The bug stamps the *caller's* org onto the token; without a real org row
    the failure is an FK violation that masks the assertion we care about.
    """
    org = Organization(name=name, created_by="test")
    db_session.add(org)
    await db_session.flush()
    return org


async def _principal(db_session, org_id):
    """An original caller with the two persisted OAuth refresh permissions."""
    person = User(
        email=f"oauth-{uuid4().hex}@contoso.example",
        organization_id=org_id,
        base_role_id=USER_ROLE_ID,
    )
    role = Role(name=f"OAuth refresh {uuid4().hex}", created_by="test")
    db_session.add_all([person, role])
    await db_session.flush()
    db_session.add_all([
        UserRole(user_id=person.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=person.id, role_id=role.id, kind="platform"),
        UserRoleBoundary(
            user_id=person.id,
            role_id=role.id,
            kind="organization",
            organization_id=org_id,
        ),
        RolePermission(role_id=role.id, permission="integrations.readwrite"),
        RolePermission(role_id=role.id, permission="secrets.read"),
    ])
    await db_session.flush()
    return UserPrincipal(
        user_id=person.id,
        email=person.email,
        organization_id=org_id,
    )


async def _principal_without_secrets(db_session, org_id):
    person = User(
        email=f"oauth-no-secrets-{uuid4().hex}@contoso.example",
        organization_id=org_id,
        base_role_id=USER_ROLE_ID,
    )
    role = Role(name=f"OAuth no secrets {uuid4().hex}", created_by="test")
    db_session.add_all([person, role])
    await db_session.flush()
    db_session.add_all([
        UserRole(user_id=person.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=person.id, role_id=role.id, kind="platform"),
        RolePermission(role_id=role.id, permission="integrations.readwrite"),
    ])
    await db_session.flush()
    return UserPrincipal(user_id=person.id, email=person.email, organization_id=org_id)


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
    ctx = ExecutionContext(
        user=await _principal(db_session, caller.id), org_id=caller.id, db=db_session
    )

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
    ctx = ExecutionContext(
        user=await _principal(db_session, caller.id), org_id=caller.id, db=db_session
    )

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

    ctx = ExecutionContext(
        user=await _principal(db_session, org.id), org_id=org.id, db=db_session
    )

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


@pytest.mark.asyncio
async def test_refresh_requires_secrets_before_token_or_provider_io(db_session, monkeypatch):
    """A role with integrations write alone cannot start a refresh or decrypt."""
    from src.routers.oauth_connections import refresh_token

    home = await _make_org(db_session, f"Contoso {uuid4().hex[:6]}")
    provider = await _make_global_cc_provider(
        db_session, connection_name=f"oauth_{uuid4().hex[:8]}"
    )
    ctx = ExecutionContext(
        user=await _principal_without_secrets(db_session, home.id),
        org_id=home.id,
        db=db_session,
    )
    token_lookup = AsyncMock(side_effect=AssertionError("token lookup ran"))
    context_builder = AsyncMock(side_effect=AssertionError("token context ran"))
    provider_io = AsyncMock(side_effect=AssertionError("provider I/O ran"))
    monkeypatch.setattr("src.repositories.oauth.OAuthProviderRepository.get_token", token_lookup)
    monkeypatch.setattr("src.routers.oauth_connections.build_token_refresh_context", context_builder)
    monkeypatch.setattr("src.routers.oauth_connections.refresh_oauth_token_http", provider_io)

    with pytest.raises(HTTPException) as error:
        await refresh_token(provider.provider_name, ctx, ctx.user)

    assert error.value.status_code == 403
    token_lookup.assert_not_awaited()
    context_builder.assert_not_awaited()
    provider_io.assert_not_awaited()


@pytest.mark.asyncio
async def test_uuid_connection_lookup_refuses_foreign_organization(db_session):
    """The repository UUID shortcut cannot disclose a provider outside reach."""
    from src.routers.oauth_connections import get_connection

    home = await _make_org(db_session, f"Contoso {uuid4().hex[:6]}")
    foreign = await _make_org(db_session, f"Fabrikam {uuid4().hex[:6]}")
    integration = Integration(name=f"Foreign integration {uuid4().hex}")
    db_session.add(integration)
    await db_session.flush()
    provider = OAuthProvider(
        provider_name=f"foreign_{uuid4().hex[:8]}",
        display_name="Foreign",
        oauth_flow_type="client_credentials",
        client_id="client",
        encrypted_client_secret=b"",
        token_url="https://login.example.com/oauth/token",
        organization_id=foreign.id,
        integration_id=integration.id,
    )
    db_session.add(provider)
    await db_session.flush()
    ctx = ExecutionContext(
        user=await _principal(db_session, home.id), org_id=home.id, db=db_session
    )

    with pytest.raises(HTTPException) as error:
        await get_connection(str(integration.id), ctx, ctx.user)

    assert error.value.status_code == 404
