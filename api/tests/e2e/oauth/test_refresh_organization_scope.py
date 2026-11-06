"""Real database scope boundaries with a fake external OAuth provider."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from shared.sdk_integrations import refresh_sdk_oauth_token
from src.core.principal import UserPrincipal
from src.models.orm import Integration, IntegrationMapping, OAuthProvider, OAuthToken, Organization
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.oauth_provider import build_token_refresh_context

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def credentials(db_session):
    orgs = [Organization(name=f"refresh-{uuid4()}", created_by="test") for _ in range(2)]
    db_session.add_all(orgs)
    integration = Integration(name=f"refresh-{uuid4()}", default_entity_id="shared-tenant")
    db_session.add(integration)
    await db_session.flush()
    provider = OAuthProvider(
        provider_name=integration.name, integration_id=integration.id,
        client_id="test-client", encrypted_client_secret=b"test-only", oauth_flow_type="client_credentials",
        token_url="https://login.example.test/{entity_id}/token", scopes=[],
    )
    db_session.add(provider)
    await db_session.flush()
    global_token = OAuthToken(provider_id=provider.id, encrypted_access_token=b"global")
    foreign_token = OAuthToken(provider_id=provider.id, organization_id=orgs[1].id,
                               encrypted_access_token=b"foreign")
    db_session.add_all([global_token, foreign_token])
    db_session.add_all([
        IntegrationMapping(integration_id=integration.id, organization_id=org.id,
                           entity_id=f"tenant-{i}") for i, org in enumerate(orgs)
    ])
    await db_session.commit()
    uid = uuid4()
    caller = Caller(
        UserPrincipal(user_id=uid, email="refresh@example.test", organization_id=orgs[0].id),
        AuthorizationContext(user_id=uid, home_organization_id=orgs[0].id,
                             base_role_id=uuid4(), is_external=False,
                             base_permissions=frozenset({"integrations.readwrite", "secrets.read"})),
    )
    integration_id, provider_id = integration.id, provider.id
    org_ids = [org.id for org in orgs]
    yield SimpleNamespace(org=orgs[0].id, caller=caller, provider=provider, provider_id=provider_id,
                          global_token=global_token, foreign_token=foreign_token)
    await db_session.rollback()
    await db_session.execute(delete(IntegrationMapping).where(IntegrationMapping.integration_id == integration_id))
    await db_session.execute(delete(OAuthToken).where(OAuthToken.provider_id == provider_id))
    await db_session.execute(delete(OAuthProvider).where(OAuthProvider.id == provider_id))
    await db_session.execute(delete(Integration).where(Integration.id == integration_id))
    await db_session.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db_session.commit()


async def test_org_acquisition_never_overwrites_global_or_foreign(db_session, credentials, monkeypatch):
    c = credentials
    async def exchange(td):
        assert td["token_url_defaults"]["entity_id"] == "tenant-0"
        return {"success": True, "access_token": "org-access",
                "encrypted_access_token": b"org-access", "expires_at": None}
    monkeypatch.setattr("src.services.oauth_provider.refresh_oauth_token_http", exchange)
    await refresh_sdk_oauth_token(db_session, connection_name=c.provider.provider_name,
                                 scope=None, caller=c.caller)
    tokens = list((await db_session.execute(select(OAuthToken).where(
        OAuthToken.provider_id == c.provider_id))).scalars())
    assert {t.organization_id: t.encrypted_access_token for t in tokens} == {
        None: b"global", c.org: b"org-access",
        c.foreign_token.organization_id: b"foreign",
    }


async def test_selected_global_refresh_keeps_global_url_and_rotates_in_place(db_session, credentials, monkeypatch):
    c = credentials
    c.provider.oauth_flow_type = "authorization_code"
    c.global_token.encrypted_refresh_token = b"old-refresh"
    await db_session.commit()
    async def exchange(td):
        assert td["token_id"] == c.global_token.id
        assert td["token_url_defaults"]["entity_id"] == "shared-tenant"
        return {"success": True, "access_token": "global-fresh",
                "encrypted_access_token": b"global-fresh",
                "encrypted_refresh_token": b"rotated", "expires_at": None}
    monkeypatch.setattr("src.services.oauth_provider.refresh_oauth_token_http", exchange)
    await refresh_sdk_oauth_token(db_session, connection_name=c.provider.provider_name,
                                 scope=None, caller=c.caller)
    await db_session.refresh(c.global_token)
    assert c.global_token.organization_id is None
    assert c.global_token.encrypted_refresh_token == b"rotated"
    assert c.foreign_token.encrypted_access_token == b"foreign"


async def test_cancelled_org_acquisition_leaves_existing_credentials_unchanged(db_session, credentials, monkeypatch):
    c = credentials
    exchange = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr("src.services.oauth_provider.refresh_oauth_token_http", exchange)
    with pytest.raises(asyncio.CancelledError):
        await refresh_sdk_oauth_token(db_session, connection_name=c.provider.provider_name,
                                     scope=None, caller=c.caller)
    await db_session.rollback()
    tokens = list((await db_session.execute(select(OAuthToken).where(
        OAuthToken.provider_id == c.provider_id))).scalars())
    assert {t.encrypted_access_token for t in tokens} == {b"global", b"foreign"}


async def test_stored_token_org_selects_url_without_caller_context(db_session, credentials):
    c = credentials
    td = await build_token_refresh_context(db_session, c.provider, c.foreign_token, org_id=None)
    assert td["token_url_defaults"]["entity_id"] == "tenant-1"
    assert td["context_error"] is None


@pytest.mark.parametrize("consumer", ["sdk", "webhook"])
async def test_new_acquisition_preserves_shared_defaults_without_mapping(
    db_session, credentials, monkeypatch, consumer
):
    from src.services.webhooks.auth import (
        build_trusted_renewal_webhook_credentials,
        resolve_webhook_integration_auth,
    )

    c = credentials
    await db_session.execute(delete(IntegrationMapping).where(
        IntegrationMapping.integration_id == c.provider.integration_id,
        IntegrationMapping.organization_id == c.org,
    ))
    await db_session.commit()

    async def exchange(td):
        assert td["context_error"] is None
        assert td["token_url_defaults"]["entity_id"] == "shared-tenant"
        return {"success": True, "access_token": "new-access",
                "encrypted_access_token": b"new-access", "expires_at": None}

    monkeypatch.setattr("src.services.oauth_provider.refresh_oauth_token_http", exchange)
    monkeypatch.setattr("src.services.webhooks.auth.refresh_oauth_token_http", exchange)
    if consumer == "sdk":
        # Repeat after persistence: the write target is not acquisition input.
        for _ in range(2):
            result = await refresh_sdk_oauth_token(
                db_session, connection_name=c.provider.provider_name,
                scope=None, caller=c.caller,
            )
            assert result["access_token"] == "new-access"
        org_token = (await db_session.execute(select(OAuthToken).where(
            OAuthToken.provider_id == c.provider_id, OAuthToken.organization_id == c.org,
        ))).scalar_one()
        assert org_token.encrypted_access_token == b"new-access"
        await db_session.refresh(c.global_token)
        assert c.global_token.organization_id is None
        assert c.global_token.encrypted_access_token == b"global"
    else:
        selected = await build_trusted_renewal_webhook_credentials(
            db_session, c.provider.integration_id, c.org,
        )
        auth = await resolve_webhook_integration_auth(selected)
        assert auth.organization_id == c.org
        assert auth.entity_id == "shared-tenant"
        assert auth.access_token == "new-access"
