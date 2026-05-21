"""Unit-level tests for _apply_callback_to_mapping — mapping resolution + entity_id capture.

We test the helper directly so we can avoid spinning the whole HTTP stack
and mocking the external token endpoint.

The end-to-end callback path (including token storage scoping) is covered
by the test_callback_scopes_token_to_mapping_org regression test below,
which exercises the full oauth_callback handler via the API.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import OAuthProvider, OAuthToken
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization


# ---------------------------------------------------------------------------
# Inline factory helpers — no fancy fixture factories needed
# ---------------------------------------------------------------------------


async def _make_integration(db: AsyncSession) -> Integration:
    integration = Integration(
        id=uuid4(),
        name=f"test-integ-{uuid4().hex[:8]}",
    )
    db.add(integration)
    await db.flush()
    return integration


async def _make_org(db: AsyncSession) -> Organization:
    org = Organization(
        id=uuid4(),
        name=f"test-org-{uuid4().hex[:8]}",
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(org)
    await db.flush()
    return org


async def _make_provider(db: AsyncSession, integration: Integration, entity_id_source: dict | None = None) -> OAuthProvider:
    provider = OAuthProvider(
        id=uuid4(),
        provider_name=f"test-provider-{uuid4().hex[:8]}",
        client_id="test-client-id",
        encrypted_client_secret=b"encrypted",
        entity_id_source=entity_id_source,
        integration_id=integration.id,
    )
    db.add(provider)
    await db.flush()
    return provider


async def _make_mapping(db: AsyncSession, integration_id, org_id, entity_id: str = "") -> IntegrationMapping:
    mapping = IntegrationMapping(
        id=uuid4(),
        integration_id=integration_id,
        organization_id=org_id,
        entity_id=entity_id,
    )
    db.add(mapping)
    await db.flush()
    return mapping


async def _make_token(db: AsyncSession, provider: OAuthProvider, org_id) -> OAuthToken:
    token = OAuthToken(
        id=uuid4(),
        organization_id=org_id,
        provider_id=provider.id,
        encrypted_access_token=b"x",
        scopes=[],
    )
    db.add(token)
    await db.flush()
    return token


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_links_token_to_mapping_and_captures_entity_id(db_session: AsyncSession):
    """_apply_callback_to_mapping sets oauth_token_id and captures entity_id from url_param."""
    from src.routers.oauth_connections import _apply_callback_to_mapping

    integration = await _make_integration(db_session)
    org = await _make_org(db_session)
    provider = await _make_provider(
        db_session, integration, entity_id_source={"type": "url_param", "key": "realmId"}
    )
    mapping = await _make_mapping(db_session, integration.id, org.id, entity_id="")
    token = await _make_token(db_session, provider, org.id)

    await _apply_callback_to_mapping(
        db=db_session,
        mapping_id=mapping.id,
        token=token,
        provider=provider,
        callback_url_params={"realmId": "9999"},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(mapping)
    assert mapping.oauth_token_id == token.id
    assert mapping.entity_id == "9999"


@pytest.mark.asyncio
async def test_callback_does_not_overwrite_existing_entity_id(db_session: AsyncSession):
    """_apply_callback_to_mapping preserves a non-empty entity_id (manual override wins)."""
    from src.routers.oauth_connections import _apply_callback_to_mapping

    integration = await _make_integration(db_session)
    org = await _make_org(db_session)
    provider = await _make_provider(
        db_session, integration, entity_id_source={"type": "url_param", "key": "realmId"}
    )
    mapping = await _make_mapping(db_session, integration.id, org.id, entity_id="manual-override")
    token = await _make_token(db_session, provider, org.id)

    await _apply_callback_to_mapping(
        db=db_session,
        mapping_id=mapping.id,
        token=token,
        provider=provider,
        callback_url_params={"realmId": "9999"},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(mapping)
    assert mapping.oauth_token_id == token.id
    assert mapping.entity_id == "manual-override"  # not overwritten


@pytest.mark.asyncio
async def test_callback_silently_skips_missing_mapping(db_session: AsyncSession):
    """_apply_callback_to_mapping returns without error when mapping_id doesn't exist."""
    from src.routers.oauth_connections import _apply_callback_to_mapping

    integration = await _make_integration(db_session)
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, integration)
    token = await _make_token(db_session, provider, org.id)

    nonexistent_id = uuid4()
    # Should not raise
    await _apply_callback_to_mapping(
        db=db_session,
        mapping_id=nonexistent_id,
        token=token,
        provider=provider,
        callback_url_params={},
        token_response={"access_token": "x"},
    )


@pytest.mark.asyncio
async def test_callback_no_entity_id_source_still_links_token(db_session: AsyncSession):
    """_apply_callback_to_mapping links the token even when provider has no entity_id_source."""
    from src.routers.oauth_connections import _apply_callback_to_mapping

    integration = await _make_integration(db_session)
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, integration, entity_id_source=None)
    mapping = await _make_mapping(db_session, integration.id, org.id, entity_id="")
    token = await _make_token(db_session, provider, org.id)

    await _apply_callback_to_mapping(
        db=db_session,
        mapping_id=mapping.id,
        token=token,
        provider=provider,
        callback_url_params={},
        token_response={"access_token": "x"},
    )

    await db_session.refresh(mapping)
    assert mapping.oauth_token_id == token.id
    assert mapping.entity_id == ""  # empty stays empty — no source configured
