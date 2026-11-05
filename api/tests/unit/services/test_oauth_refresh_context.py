"""Tests for build_token_refresh_context entity_id fallback chain.

This is the single source of truth for ``{entity_id}`` placeholder resolution
in OAuth refresh URL templates. The fallback chain is:

  1. the selected token's same-organization mapping
  2. the effective organization mapping for legacy token links
  3. integration defaults for global credentials

These tests pin the chain so the scheduler, SDK endpoint, and oauth_connections
router cannot drift.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4


def _make_provider(
    *,
    integration_id=None,
    token_url_defaults=None,
):
    provider = MagicMock()
    provider.id = uuid4()
    provider.integration_id = integration_id
    provider.provider_name = "TestProvider"
    provider.oauth_flow_type = "client_credentials"
    provider.client_id = "client-id"
    provider.encrypted_client_secret = b"encrypted-secret"
    provider.token_url = "https://login.example.com/{entity_id}/token"
    provider.token_url_defaults = token_url_defaults
    provider.scopes = ["read"]
    provider.audience = None
    return provider


def _make_integration(default_entity_id=None, entity_id=None):
    integration = MagicMock()
    integration.default_entity_id = default_entity_id
    integration.entity_id = entity_id
    return integration


def _make_mapping(entity_id):
    mapping = MagicMock()
    mapping.entity_id = entity_id
    return mapping


def _stub_db(*, integration=None, mapping=None):
    """Build an AsyncMock db whose .execute returns canned results.

    When ``mapping`` is provided, the FIRST call returns the mapping (this is
    the org-mapping lookup) and the SECOND returns the integration (the
    fallback query issued only if the mapping lookup came up empty, which
    does NOT happen in that case — but we still stub it for safety).

    When only ``integration`` is provided, every call returns the integration
    (used for the no-org_id code path which only issues one query).
    """
    mapping_result = MagicMock()
    mapping_result.scalar_one_or_none.return_value = mapping

    integration_result = MagicMock()
    integration_result.scalar_one_or_none.return_value = integration

    # build_token_refresh_context short-circuits the integration query when
    # a mapping with a truthy entity_id is found, so in that case we only
    # ever stub one result. Otherwise (no org_id, or org_id with a null/None
    # mapping) the integration query runs instead.
    if mapping is not None and getattr(mapping, "entity_id", None):
        results = [mapping_result]
    else:
        results = [integration_result]

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=results)
    return db


class TestBuildTokenRefreshContextEntityIdFallback:
    """Pin the {entity_id} fallback chain in one place."""

    @pytest.mark.asyncio
    async def test_no_integration_means_no_entity_id(self):
        """Providers without a linked integration carry no entity_id."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(integration_id=None)
        db = AsyncMock()

        td = await build_token_refresh_context(db=db, provider=provider, org_id=None)

        assert "entity_id" not in td["token_url_defaults"]
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_integration_default_entity_id_wins_when_no_mapping(self):
        """With no org_id, ``default_entity_id`` is the primary source."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(integration_id=uuid4())
        integration = _make_integration(default_entity_id="default-tenant", entity_id="fallback-tenant")
        db = _stub_db(integration=integration)

        td = await build_token_refresh_context(db=db, provider=provider, org_id=None)

        assert td["token_url_defaults"]["entity_id"] == "default-tenant"

    @pytest.mark.asyncio
    async def test_falls_back_to_integration_entity_id_when_default_missing(self):
        """The PR #21 regression: ``entity_id`` is used when ``default_entity_id`` is None."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(integration_id=uuid4())
        integration = _make_integration(default_entity_id=None, entity_id="fallback-tenant")
        db = _stub_db(integration=integration)

        td = await build_token_refresh_context(db=db, provider=provider, org_id=None)

        assert td["token_url_defaults"]["entity_id"] == "fallback-tenant"

    @pytest.mark.asyncio
    async def test_no_entity_id_at_all_leaves_placeholder_unresolved(self):
        """When nothing is set, the defaults dict has no entity_id key."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(integration_id=uuid4())
        integration = _make_integration(default_entity_id=None, entity_id=None)
        db = _stub_db(integration=integration)

        td = await build_token_refresh_context(db=db, provider=provider, org_id=None)

        assert "entity_id" not in td["token_url_defaults"]

    @pytest.mark.asyncio
    async def test_org_mapping_beats_integration_default(self):
        """With an org_id supplied, a matching org mapping wins over integration defaults."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(integration_id=uuid4())
        mapping = _make_mapping(entity_id="org-specific-tenant")
        integration = _make_integration(default_entity_id="default-tenant")
        db = _stub_db(mapping=mapping, integration=integration)

        td = await build_token_refresh_context(
            db=db, provider=provider, org_id=uuid4()
        )

        assert td["token_url_defaults"]["entity_id"] == "org-specific-tenant"

    @pytest.mark.asyncio
    async def test_token_mapping_beats_provider_and_integration_defaults(self):
        """A token's own tenant mapping is authoritative for refresh URLs."""
        from src.services.oauth_provider import build_token_refresh_context

        org_id = uuid4()
        provider = _make_provider(
            integration_id=uuid4(),
            token_url_defaults={"entity_id": "provider-default"},
        )
        token = MagicMock()
        token.id = uuid4()
        token.organization_id = org_id
        token.encrypted_refresh_token = None
        mapping = _make_mapping(entity_id="token-tenant")
        mapping_result = MagicMock()
        mapping_result.scalar_one_or_none.return_value = mapping
        db = AsyncMock()
        db.execute = AsyncMock(return_value=mapping_result)

        td = await build_token_refresh_context(
            db=db, provider=provider, token=token, org_id=None
        )

        assert td["token_url_defaults"]["entity_id"] == "token-tenant"
        assert td["context_error"] is None
        db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_selected_global_token_keeps_global_integration_default(self):
        """Caller org authorization does not rescope a selected global token."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(
            integration_id=uuid4(),
            token_url_defaults={"custom_key": "preserved"},
        )
        token = MagicMock()
        token.id = uuid4()
        token.organization_id = None
        token.encrypted_refresh_token = b"encrypted-refresh"
        no_mapping = MagicMock()
        no_mapping.scalar_one_or_none.return_value = None
        integration_result = MagicMock()
        integration_result.scalar_one_or_none.return_value = _make_integration(
            default_entity_id="global-tenant"
        )
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[no_mapping, integration_result])

        td = await build_token_refresh_context(
            db=db,
            provider=provider,
            token=token,
            org_id=uuid4(),
        )

        assert td["token_url_defaults"] == {
            "custom_key": "preserved",
            "entity_id": "global-tenant",
        }
        assert td["context_error"] is None
        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_tenant_token_without_mapping_fails_closed(self):
        """A tenant token cannot inherit a provider-wide entity_id default."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(
            integration_id=uuid4(),
            token_url_defaults={"entity_id": "wrong-default"},
        )
        token = MagicMock()
        token.id = uuid4()
        token.organization_id = uuid4()
        token.encrypted_refresh_token = None
        no_mapping = MagicMock()
        no_mapping.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[no_mapping, no_mapping])

        td = await build_token_refresh_context(
            db=db, provider=provider, token=token, org_id=None
        )

        assert "entity_id" not in td["token_url_defaults"]
        assert td["context_error"] == (
            "Tenant-scoped OAuth token has no matching integration mapping "
            "entity_id"
        )

    @pytest.mark.asyncio
    async def test_explicit_org_cannot_override_token_org(self):
        """Conflicting tenant inputs fail before mapping or provider access."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(
            integration_id=uuid4(),
            token_url_defaults={"entity_id": "wrong-default"},
        )
        token = MagicMock()
        token.id = uuid4()
        token.organization_id = uuid4()
        token.encrypted_refresh_token = None
        db = AsyncMock()

        td = await build_token_refresh_context(
            db=db, provider=provider, token=token, org_id=uuid4()
        )

        assert "entity_id" not in td["token_url_defaults"]
        assert td["context_error"] == (
            "OAuth token organization does not match refresh context"
        )
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refresh_does_not_call_provider_when_context_is_unsafe(self):
        """Context resolution failures stop before any provider request."""
        from src.services.oauth_provider import refresh_oauth_token_http

        td = {
            "token_id": uuid4(),
            "provider_id": uuid4(),
            "provider_name": "TestProvider",
            "context_error": "tenant mapping unresolved",
        }

        outcome = await refresh_oauth_token_http(td)

        assert outcome == {
            "token_id": td["token_id"],
            "provider_id": td["provider_id"],
            "success": False,
            "error": "tenant mapping unresolved",
        }

    @pytest.mark.asyncio
    async def test_existing_token_url_defaults_are_preserved(self):
        """Resolved entity_id is merged into, not replacing, the provider's defaults."""
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(
            integration_id=uuid4(),
            token_url_defaults={"custom_key": "custom_value"},
        )
        integration = _make_integration(default_entity_id="tenant-abc")
        db = _stub_db(integration=integration)

        td = await build_token_refresh_context(db=db, provider=provider, org_id=None)

        assert td["token_url_defaults"]["entity_id"] == "tenant-abc"
        assert td["token_url_defaults"]["custom_key"] == "custom_value"


class TestBuildTokenRefreshContextShape:
    """Ensure the returned dict matches what refresh_oauth_token_http expects."""

    @pytest.mark.asyncio
    async def test_context_dict_has_all_required_keys(self):
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(integration_id=None)
        token = MagicMock()
        token.id = uuid4()
        token.encrypted_refresh_token = b"encrypted-refresh"

        db = AsyncMock()
        td = await build_token_refresh_context(
            db=db, provider=provider, token=token, org_id=None
        )

        # These are the keys refresh_oauth_token_http reads.
        required = {
            "token_id",
            "provider_id",
            "provider_name",
            "oauth_flow_type",
            "client_id",
            "encrypted_client_secret",
            "token_url",
            "token_url_defaults",
            "scopes",
            "audience",
            "encrypted_refresh_token",
        }
        assert required.issubset(td.keys())
        assert td["token_id"] == token.id
        assert td["encrypted_refresh_token"] == b"encrypted-refresh"

    @pytest.mark.asyncio
    async def test_no_token_produces_none_refresh_fields(self):
        from src.services.oauth_provider import build_token_refresh_context

        provider = _make_provider(integration_id=None)
        db = AsyncMock()

        td = await build_token_refresh_context(db=db, provider=provider, token=None, org_id=None)

        assert td["token_id"] is None
        assert td["encrypted_refresh_token"] is None
