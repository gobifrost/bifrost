"""Tests for tenant-scoped webhook OAuth resolution."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.webhooks.auth import (
    WebhookIntegrationCredentials,
    build_webhook_integration_credentials,
    resolve_webhook_integration_auth,
)


@pytest.mark.asyncio
async def test_credentials_require_and_use_exact_organization_mapping(monkeypatch):
    integration_id = uuid4()
    organization_id = uuid4()
    provider = SimpleNamespace(oauth_flow_type="client_credentials")
    integration = SimpleNamespace(
        id=integration_id,
        name="Microsoft",
        oauth_provider=provider,
    )
    mapping = SimpleNamespace(entity_id="tenant-from-mapping")
    integration_result = MagicMock()
    integration_result.scalar_one_or_none.return_value = integration
    mapping_result = MagicMock()
    mapping_result.scalar_one_or_none.return_value = mapping
    db = AsyncMock()
    db.execute.side_effect = [integration_result, mapping_result]
    token_context = {"token_url_defaults": {"entity_id": mapping.entity_id}}
    build_context = AsyncMock(return_value=token_context)
    monkeypatch.setattr(
        "src.services.webhooks.auth.build_token_refresh_context",
        build_context,
    )

    credentials = await build_webhook_integration_credentials(
        db,
        integration_id,
        organization_id,
    )

    assert credentials.entity_id == "tenant-from-mapping"
    assert credentials.organization_id == organization_id
    assert credentials.token_context == token_context
    build_context.assert_awaited_once_with(
        db,
        provider,
        token=None,
        org_id=organization_id,
    )


@pytest.mark.asyncio
async def test_credentials_reject_missing_organization_mapping(monkeypatch):
    integration_id = uuid4()
    provider = SimpleNamespace(oauth_flow_type="client_credentials")
    integration = SimpleNamespace(
        id=integration_id,
        name="Microsoft",
        oauth_provider=provider,
    )
    integration_result = MagicMock()
    integration_result.scalar_one_or_none.return_value = integration
    mapping_result = MagicMock()
    mapping_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute.side_effect = [integration_result, mapping_result]

    with pytest.raises(ValueError, match="not mapped to the selected organization"):
        await build_webhook_integration_credentials(db, integration_id, uuid4())


@pytest.mark.asyncio
async def test_client_credentials_token_uses_mapping_identity(monkeypatch):
    credentials = WebhookIntegrationCredentials(
        integration_id=uuid4(),
        organization_id=uuid4(),
        entity_id="tenant-from-mapping",
        token_context={"provider_id": uuid4()},
    )
    refresh = AsyncMock(
        return_value={"success": True, "access_token": "fresh-tenant-token"}
    )
    monkeypatch.setattr(
        "src.services.webhooks.auth.refresh_oauth_token_http",
        refresh,
    )

    auth = await resolve_webhook_integration_auth(credentials)

    assert auth.entity_id == "tenant-from-mapping"
    assert auth.organization_id == credentials.organization_id
    assert auth.access_token == "fresh-tenant-token"
    refresh.assert_awaited_once_with(credentials.token_context)


@pytest.mark.asyncio
async def test_valid_stored_token_does_not_refresh(monkeypatch):
    monkeypatch.setattr(
        "src.services.webhooks.auth.decrypt_secret",
        lambda _value: "stored-token",
    )
    refresh = AsyncMock()
    monkeypatch.setattr(
        "src.services.webhooks.auth.refresh_oauth_token_http",
        refresh,
    )
    credentials = WebhookIntegrationCredentials(
        integration_id=uuid4(),
        organization_id=uuid4(),
        entity_id="tenant-from-mapping",
        token_context={},
        encrypted_access_token=b"encrypted",
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    auth = await resolve_webhook_integration_auth(credentials)

    assert auth.access_token == "stored-token"
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_token_request_surfaces_provider_error(monkeypatch):
    credentials = WebhookIntegrationCredentials(
        integration_id=uuid4(),
        organization_id=uuid4(),
        entity_id="tenant-from-mapping",
        token_context={},
    )
    monkeypatch.setattr(
        "src.services.webhooks.auth.refresh_oauth_token_http",
        AsyncMock(return_value={"success": False, "error": "invalid client secret"}),
    )

    with pytest.raises(ValueError, match="invalid client secret"):
        await resolve_webhook_integration_auth(credentials)
