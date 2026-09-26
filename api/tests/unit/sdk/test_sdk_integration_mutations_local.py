"""Facade tests for ``integrations`` mutations via ``BifrostClient.engine_request``."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-mut-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=False,
        created_by="sdk-mut-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_provider(db_session, integration, *, org_id=None):
    from src.core.security import encrypt_secret
    from src.models.orm.oauth import OAuthProvider

    provider = OAuthProvider(
        provider_name=f"prov-{uuid4().hex[:8]}",
        client_id="test-client",
        encrypted_client_secret=encrypt_secret("test-client-secret").encode(),
        oauth_flow_type="client_credentials",
        token_url="https://example.com/token",
        token_url_defaults={},
        scopes=["read"],
        integration_id=integration.id if integration else None,
        organization_id=org_id,
    )
    db_session.add(provider)
    await db_session.flush()
    return provider


@pytest.mark.asyncio
class TestOAuthProviderLookupScope:
    """Org-scoped provider lookup never cascades to a global provider."""

    async def test_org_only_provider_lookup_never_cascades(self, db_session):
        from src.models.orm.integrations import Integration
        from src.repositories.oauth import OAuthProviderRepository

        org = await _seed_org(db_session)
        integration = Integration(name=f"lookup-{uuid4().hex[:8]}")
        db_session.add(integration)
        await db_session.flush()

        global_provider = await _seed_provider(db_session, integration)
        repo = OAuthProviderRepository(
            db_session, org_id=org.id, is_superuser=False, is_external=True
        )
        assert (
            await repo.get_org_level_by_provider_name(global_provider.provider_name)
            is None
        )

        own_provider = await _seed_provider(db_session, None, org_id=org.id)
        assert (
            await repo.get_org_level_by_provider_name(own_provider.provider_name)
            == own_provider
        )


class TestFacadeEngineRequestMutations:
    """upsert/delete/refresh ride ``BifrostClient.engine_request``.

    The shared client owns transport selection (worker socket vs network)
    and the no-failure-replay contract; the facade keeps its HTTP-shaped
    ``RuntimeError`` mapping for upsert/refresh. The dedicated-channel round
    trips these tests used to drive were replaced by the real socket round
    trips in ``tests/unit/services/test_worker_sdk_http.py`` and
    ``tests/unit/execution/test_worker_sdk_http_fork.py``.
    """

    @pytest.mark.asyncio
    async def test_upsert_and_delete_use_engine_request(self):
        from bifrost.integrations import integrations

        item = {
            "id": "mid", "integration_id": "iid", "organization_id": "oid",
            "entity_id": "ent-1", "entity_name": None, "oauth_token_id": None,
            "config": {"region": "eu"},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        client = AsyncMock()
        client.engine_request = AsyncMock(
            side_effect=[
                httpx.Response(
                    200, json=item,
                    request=httpx.Request(
                        "POST",
                        "http://api/api/sdk/integrations/upsert_mapping",
                    ),
                ),
                httpx.Response(
                    200, json={"deleted": True},
                    request=httpx.Request(
                        "POST",
                        "http://api/api/sdk/integrations/delete_mapping",
                    ),
                ),
            ]
        )
        with patch("bifrost.integrations.get_client", return_value=client):
            mapping = await integrations.upsert_mapping(
                "P", scope="oid", entity_id="ent-1",
                config={"region": "eu"},
            )
            deleted = await integrations.delete_mapping("P", scope="oid")

        assert mapping.entity_id == "ent-1"
        assert mapping.config == {"region": "eu"}
        assert deleted is True
        assert [
            call.args[1] for call in client.engine_request.await_args_list
        ] == [
            "/api/sdk/integrations/upsert_mapping",
            "/api/sdk/integrations/delete_mapping",
        ]

    @pytest.mark.asyncio
    async def test_upsert_error_maps_to_runtime_error(self):
        from bifrost.integrations import integrations

        client = AsyncMock()
        client.engine_request = AsyncMock(
            return_value=httpx.Response(
                404,
                json={"detail": "Integration 'P' not found"},
                request=httpx.Request(
                    "POST", "http://api/api/sdk/integrations/upsert_mapping"
                ),
            )
        )
        with patch("bifrost.integrations.get_client", return_value=client):
            with pytest.raises(RuntimeError, match="Failed to upsert mapping"):
                await integrations.upsert_mapping(
                    "P", scope="oid", entity_id="ent-1"
                )
        assert client.engine_request.await_count == 1

    @pytest.mark.asyncio
    async def test_refresh_registers_secret_through_engine_request(self):
        from bifrost._context import (
            clear_execution_context,
            set_execution_context,
        )
        from bifrost.models import OAuthCredentials
        from src.sdk.context import ExecutionContext

        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="T", scope="org-1",
            organization=None, is_platform_admin=False,
            is_function_key=False, execution_id="exec-1",
        )
        set_execution_context(ctx)
        try:
            client = AsyncMock()
            client.engine_request = AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={"access_token": "fresh-tok-live", "expires_at": None},
                    request=httpx.Request(
                        "POST",
                        "http://api/api/sdk/integrations/refresh_token",
                    ),
                )
            )
            creds = OAuthCredentials(
                connection_name="P", client_id="cid", client_secret=None,
                authorization_url=None, token_url=None, scopes=[],
                access_token="stale", refresh_token="ref",
                expires_at=None,
            )
            with patch("bifrost.client.get_client", return_value=client):
                out = await creds.refresh()

            assert out is creds
            assert creds.access_token == "fresh-tok-live"
            assert "fresh-tok-live" in ctx._collect_secret_values()
            client.engine_request.assert_awaited_once_with(
                "POST",
                "/api/sdk/integrations/refresh_token",
                json={"connection_name": "P"},
            )
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_refresh_error_maps_to_runtime_error(self):
        from bifrost.models import OAuthCredentials

        client = AsyncMock()
        client.engine_request = AsyncMock(
            return_value=httpx.Response(
                502,
                json={"detail": "provider down"},
                request=httpx.Request(
                    "POST", "http://api/api/sdk/integrations/refresh_token"
                ),
            )
        )
        creds = OAuthCredentials(
            connection_name="P", client_id="cid", client_secret=None,
            authorization_url=None, token_url=None, scopes=[],
            access_token="stale", refresh_token="ref",
            expires_at=None,
        )
        with patch("bifrost.client.get_client", return_value=client):
            with pytest.raises(RuntimeError, match="Token refresh failed"):
                await creds.refresh()
        assert client.engine_request.await_count == 1
