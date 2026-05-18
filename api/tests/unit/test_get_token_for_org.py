"""Unit tests for get_token_for_org — mapping-first runtime token lookup."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import OAuthProvider, OAuthToken
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Inline factory helpers (same pattern as test_oauth_per_mapping_callback.py)
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


async def _make_provider(db: AsyncSession, integration: Integration) -> OAuthProvider:
    provider = OAuthProvider(
        id=uuid4(),
        provider_name=f"test-provider-{uuid4().hex[:8]}",
        client_id="test-client-id",
        encrypted_client_secret=b"encrypted",
        integration_id=integration.id,
    )
    db.add(provider)
    await db.flush()
    return provider


async def _make_mapping(
    db: AsyncSession,
    integration_id,
    org_id,
    oauth_token_id=None,
) -> IntegrationMapping:
    mapping = IntegrationMapping(
        id=uuid4(),
        integration_id=integration_id,
        organization_id=org_id,
        entity_id="",
        oauth_token_id=oauth_token_id,
    )
    db.add(mapping)
    await db.flush()
    return mapping


async def _make_token(
    db: AsyncSession,
    provider: OAuthProvider,
    org_id,
) -> OAuthToken:
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


async def _make_integration_level_token(
    db: AsyncSession,
    provider: OAuthProvider,
) -> OAuthToken:
    """Integration-level token: organization_id IS NULL."""
    token = OAuthToken(
        id=uuid4(),
        organization_id=None,
        provider_id=provider.id,
        encrypted_access_token=b"fallback",
        scopes=[],
    )
    db.add(token)
    await db.flush()
    return token


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_mapping_with_own_token_wins(db_session: AsyncSession):
    """Mapping has its own oauth_token_id — that token is returned, not the fallback."""
    from src.services.oauth_provider import get_token_for_org

    integration = await _make_integration(db_session)
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, integration)

    # Integration-level fallback token (org IS NULL)
    fallback_token = await _make_integration_level_token(db_session, provider)

    # Mapping-scoped token (linked to org, but token has org_id)
    mapping_token = await _make_token(db_session, provider, org.id)

    # Mapping explicitly linked to mapping_token
    await _make_mapping(db_session, integration.id, org.id, oauth_token_id=mapping_token.id)

    result = await get_token_for_org(db_session, integration.id, org.id)

    assert result is not None
    assert result.id == mapping_token.id
    assert result.id != fallback_token.id


async def test_falls_back_to_integration_token_when_mapping_unlinked(db_session: AsyncSession):
    """Mapping exists but oauth_token_id is None — integration-level token returned."""
    from src.services.oauth_provider import get_token_for_org

    integration = await _make_integration(db_session)
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, integration)

    # Mapping exists but has no token linked
    await _make_mapping(db_session, integration.id, org.id, oauth_token_id=None)

    # Integration-level fallback token
    fallback_token = await _make_integration_level_token(db_session, provider)

    result = await get_token_for_org(db_session, integration.id, org.id)

    assert result is not None
    assert result.id == fallback_token.id


async def test_falls_back_to_integration_token_when_no_mapping(db_session: AsyncSession):
    """No mapping exists for (integration, org) — integration-level token returned."""
    from src.services.oauth_provider import get_token_for_org

    integration = await _make_integration(db_session)
    org = await _make_org(db_session)
    provider = await _make_provider(db_session, integration)

    # No mapping at all

    # Integration-level fallback token
    fallback_token = await _make_integration_level_token(db_session, provider)

    result = await get_token_for_org(db_session, integration.id, org.id)

    assert result is not None
    assert result.id == fallback_token.id


async def test_returns_none_when_nothing_connected(db_session: AsyncSession):
    """No mapping, no integration-level token — None returned."""
    from src.services.oauth_provider import get_token_for_org

    integration = await _make_integration(db_session)
    org = await _make_org(db_session)
    await _make_provider(db_session, integration)

    # No mapping, no tokens

    result = await get_token_for_org(db_session, integration.id, org.id)

    assert result is None
