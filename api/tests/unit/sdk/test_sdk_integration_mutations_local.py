"""Stage 2c: HTTP/local parity for ``integrations.upsert_mapping``,
``integrations.delete_mapping``, and ``OAuthCredentials.refresh()``.

Covers the acceptance surface that does not need a forked child:

- the shared service (``shared.sdk_integrations``) applies the mutation
  scope gate, missing-integration ordering, OAuth-link preservation,
  config-write/merged-echo behavior, external-user isolation, and the
  locked refresh lookup + rotation + persistence exactly like the HTTP
  handler (the handler delegates to it);
- the parent dispatcher calls that same service with a parent-derived
  actor and maps scope/service failures to HTTP-style statuses;
- the child transport performs real mutation/refresh round trips over a
  dedicated channel with zero HTTP requests, secret registration, and
  the same public error shapes as the HTTP facades (``RuntimeError``
  for upsert/refresh) without HTTP fallback.
"""

import asyncio
import contextlib
import json
import multiprocessing
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.models.contracts.cli import (
    SDKIntegrationsDeleteMappingRequest,
    SDKIntegrationsRefreshTokenRequest,
    SDKIntegrationsUpsertMappingRequest,
)


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import (
        LocalDispatchPrincipal,
    )

    return LocalDispatchPrincipal(
        caller_org_id=org_id,
        is_platform_admin=kwargs.get("is_platform_admin", False),
        is_provider_org=kwargs.get("is_provider_org", False),
        is_external=kwargs.get("is_external", False),
        actor_email=kwargs.get("actor_email", "sdk-local@test.local"),
        solution_id=kwargs.get("solution_id"),
    )


class _StubUser:
    def __init__(
        self,
        organization_id=None,
        is_superuser=False,
        is_external=False,
        email="sdk-local@test.local",
    ):
        self.organization_id = organization_id
        self.is_superuser = is_superuser
        self.is_external = is_external
        self.email = email


async def _seed_org(db_session, *, is_provider=False):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-mut-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-mut-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_integration(db_session, name, *, defaults=None):
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm import Config as ConfigModel
    from src.models.orm.integrations import Integration as IntegrationModel

    integration = IntegrationModel(name=name)
    db_session.add(integration)
    await db_session.flush()
    for key, value in (defaults or {}).items():
        db_session.add(
            ConfigModel(
                key=key,
                value={"value": value},
                config_type=ConfigTypeEnum.STRING,
                organization_id=None,
                integration_id=integration.id,
                updated_by="sdk-mut-test",
            )
        )
    await db_session.flush()
    return integration


async def _seed_secret_default(db_session, integration, key, value):
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm import Config as ConfigModel
    from src.core.security import encrypt_secret

    db_session.add(
        ConfigModel(
            key=key,
            value={"value": encrypt_secret(value)},
            config_type=ConfigTypeEnum.SECRET,
            organization_id=None,
            integration_id=integration.id,
            updated_by="sdk-mut-test",
        )
    )
    await db_session.flush()


async def _seed_mapping(
    db_session, integration, org_id, *, entity_id, overrides=None,
    oauth_token_id=None,
):
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm import Config as ConfigModel
    from src.models.orm.integrations import IntegrationMapping as MappingModel

    mapping = MappingModel(
        integration_id=integration.id,
        organization_id=org_id,
        entity_id=entity_id,
        entity_name=f"entity-{entity_id[:8]}",
        oauth_token_id=oauth_token_id,
    )
    db_session.add(mapping)
    await db_session.flush()
    for key, value in (overrides or {}).items():
        db_session.add(
            ConfigModel(
                key=key,
                value={"value": value},
                config_type=ConfigTypeEnum.STRING,
                organization_id=org_id,
                integration_id=integration.id,
                updated_by="sdk-mut-test",
            )
        )
    await db_session.flush()
    return mapping


async def _seed_provider(
    db_session, integration, *, org_id=None, flow="client_credentials",
    token_url="https://example.com/token",
):
    from src.core.security import encrypt_secret
    from src.models.orm.oauth import OAuthProvider

    provider = OAuthProvider(
        provider_name=f"prov-{uuid4().hex[:8]}",
        client_id="test-client",
        encrypted_client_secret=encrypt_secret("test-client-secret").encode(),
        oauth_flow_type=flow,
        token_url=token_url,
        token_url_defaults={},
        scopes=["read"],
        integration_id=integration.id if integration else None,
        organization_id=org_id,
    )
    db_session.add(provider)
    await db_session.flush()
    return provider


async def _seed_token(
    db_session, provider, *, org_id=None, access="stored-access",
    refresh="stored-refresh",
):
    from src.core.security import encrypt_secret
    from src.models.orm.oauth import OAuthToken

    token = OAuthToken(
        organization_id=org_id,
        provider_id=provider.id,
        user_id=None,
        encrypted_access_token=encrypt_secret(access).encode(),
        encrypted_refresh_token=(
            encrypt_secret(refresh).encode() if refresh is not None else None
        ),
        scopes=["read"],
    )
    db_session.add(token)
    await db_session.flush()
    return token


def _refresh_outcome(*, access="new-access", refresh="new-refresh"):
    from src.core.security import encrypt_secret

    return {
        "success": True,
        "access_token": access,
        "encrypted_access_token": encrypt_secret(access).encode(),
        "encrypted_refresh_token": encrypt_secret(refresh).encode(),
        "refresh_token": refresh,
        "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


async def _http_upsert(db_session, *, user, name, scope, entity_id,
                       entity_name=None, config=None):
    from src.routers.cli import sdk_integrations_upsert_mapping

    return await sdk_integrations_upsert_mapping(
        SDKIntegrationsUpsertMappingRequest(
            name=name, scope=scope, entity_id=entity_id,
            entity_name=entity_name, config=config,
        ),
        user,
        db_session,
    )


async def _local_upsert(db_session, *, principal, name, scope, entity_id,
                        entity_name=None, config=None):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {"v": 1, "id": "up-1", "op": "integrations.upsert_mapping",
         "name": name, "scope": scope, "entity_id": entity_id,
         "entity_name": entity_name, "config": config},
    )


async def _http_delete(db_session, *, user, name, scope):
    from src.routers.cli import sdk_integrations_delete_mapping

    return await sdk_integrations_delete_mapping(
        SDKIntegrationsDeleteMappingRequest(name=name, scope=scope),
        user,
        db_session,
    )


async def _local_delete(db_session, *, principal, name, scope):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {"v": 1, "id": "del-1", "op": "integrations.delete_mapping",
         "name": name, "scope": scope},
    )


async def _http_refresh(db_session, *, user, connection_name, scope=None):
    from src.routers.cli import sdk_integrations_refresh_token

    return await sdk_integrations_refresh_token(
        SDKIntegrationsRefreshTokenRequest(
            connection_name=connection_name, scope=scope
        ),
        user,
        db_session,
    )


async def _local_refresh(db_session, *, principal, connection_name, scope=None):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {"v": 1, "id": "ref-1", "op": "integrations.refresh_token",
         "connection_name": connection_name, "scope": scope},
    )


@pytest.mark.asyncio
class TestUpsertParity:
    async def test_create_then_update_match_with_merged_echo(self, db_session):
        tag = uuid4().hex[:8]
        org_h = await _seed_org(db_session)
        org_l = await _seed_org(db_session)
        integ_h = await _seed_integration(
            db_session, f"up-h-{tag}", defaults={"region": "us"}
        )
        integ_l = await _seed_integration(
            db_session, f"up-l-{tag}", defaults={"region": "us"}
        )
        user_h = _StubUser(organization_id=org_h.id)
        principal_l = _principal(org_l.id)

        http_created = await _http_upsert(
            db_session, user=user_h, name=integ_h.name, scope=str(org_h.id),
            entity_id="tenant-1", entity_name="Tenant One",
            config={"region": "eu"},
        )
        local_created = await _local_upsert(
            db_session, principal=principal_l, name=integ_l.name,
            scope=str(org_l.id), entity_id="tenant-1",
            entity_name="Tenant One", config={"region": "eu"},
        )
        assert local_created["ok"] is True, local_created
        assert local_created["result"]["entity_id"] == "tenant-1"
        assert local_created["result"]["entity_name"] == "Tenant One"
        # Merged echo: org override wins over the global default.
        assert local_created["result"]["config"] == {"region": "eu"}
        assert local_created["result"]["config"] == http_created.config
        assert local_created["result"]["organization_id"] == str(org_l.id)

        http_updated = await _http_upsert(
            db_session, user=user_h, name=integ_h.name, scope=str(org_h.id),
            entity_id="tenant-1b", entity_name=None, config={"region": "ap"},
        )
        local_updated = await _local_upsert(
            db_session, principal=principal_l, name=integ_l.name,
            scope=str(org_l.id), entity_id="tenant-1b",
            entity_name=None, config={"region": "ap"},
        )
        assert local_updated["ok"] is True, local_updated
        # Same mapping row, new entity id, merged echo follows the write.
        assert local_updated["result"]["id"] == local_created["result"]["id"]
        assert local_updated["result"]["entity_id"] == "tenant-1b"
        assert local_updated["result"]["config"] == {"region": "ap"}
        assert local_updated["result"]["config"] == http_updated.config
        # Committed state is visible to a fresh read on both paths.
        from src.repositories.integrations import IntegrationsRepository

        repo = IntegrationsRepository(db_session)
        row = await repo.get_mapping_by_org(integ_l.id, org_l.id)
        assert row is not None
        assert row.entity_id == "tenant-1b"

    async def test_update_preserves_oauth_link(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"up-link-{tag}")
        provider = await _seed_provider(db_session, integration)
        token = await _seed_token(db_session, provider, org_id=org.id)
        await _seed_mapping(
            db_session, integration, org.id, entity_id="tenant-link",
            oauth_token_id=token.id,
        )
        user = _StubUser(organization_id=org.id)

        http_value = await _http_upsert(
            db_session, user=user, name=integration.name,
            scope=str(org.id), entity_id="tenant-link-2",
            config={"k": "v"},
        )
        assert http_value.oauth_token_id == str(token.id)

        # Re-seed the link (the HTTP write above already moved this row, so
        # assert the local path on a second org mapping instead).
        org2 = await _seed_org(db_session)
        await _seed_mapping(
            db_session, integration, org2.id, entity_id="tenant-link",
            oauth_token_id=token.id,
        )
        principal2 = _principal(org2.id)
        local = await _local_upsert(
            db_session, principal=principal2, name=integration.name,
            scope=str(org2.id), entity_id="tenant-link-2",
            config={"k": "v"},
        )
        assert local["ok"] is True, local
        assert local["result"]["oauth_token_id"] == str(token.id)

    async def test_missing_integration_is_404_before_scope(self, db_session):
        import fastapi

        user = _StubUser(organization_id=(await _seed_org(db_session)).id)
        principal = _principal(user.organization_id)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_upsert(
                db_session, user=user, name="missing", scope=str(uuid4()),
                entity_id="ent-1",
            )
        assert exc_info.value.status_code == 404
        local = await _local_upsert(
            db_session, principal=principal, name="missing",
            scope=str(uuid4()), entity_id="ent-1",
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_global_scope_is_400(self, db_session):
        import fastapi

        tag = uuid4().hex[:8]
        integration = await _seed_integration(db_session, f"up-glob-{tag}")
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_upsert(
                db_session, user=user, name=integration.name,
                scope="global", entity_id="ent-1",
            )
        assert exc_info.value.status_code == 400
        local = await _local_upsert(
            db_session, principal=principal, name=integration.name,
            scope="global", entity_id="ent-1",
        )
        assert local["ok"] is False
        assert local["status"] == 400

    async def test_cross_org_denied(self, db_session):
        import fastapi

        tag = uuid4().hex[:8]
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"up-denied-{tag}")
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_upsert(
                db_session, user=user, name=integration.name,
                scope=str(org_b.id), entity_id="ent-1",
            )
        assert exc_info.value.status_code == 403
        local = await _local_upsert(
            db_session, principal=principal, name=integration.name,
            scope=str(org_b.id), entity_id="ent-1",
        )
        assert local["ok"] is False
        assert local["status"] == 403

    async def test_external_echo_drops_global_tier(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"up-ext-{tag}")
        await _seed_secret_default(
            db_session, integration, "api_key", "global-secret-canary"
        )
        user = _StubUser(organization_id=org.id, is_external=True)
        principal = _principal(org.id, is_external=True)
        http_value = await _http_upsert(
            db_session, user=user, name=integration.name,
            scope=str(org.id), entity_id="tenant-ext",
            config={"region": "eu"},
        )
        local = await _local_upsert(
            db_session, principal=principal, name=integration.name,
            scope=str(org.id), entity_id="tenant-ext",
            config={"region": "eu"},
        )
        assert local["ok"] is True, local
        assert local["result"]["config"] == http_value.config
        blob = json.dumps(local["result"])
        assert "global-secret-canary" not in blob
        assert local["result"]["config"] == {"region": "eu"}


@pytest.mark.asyncio
class TestDeleteParity:
    async def test_delete_existing_matches(self, db_session):
        tag = uuid4().hex[:8]
        org_h = await _seed_org(db_session)
        org_l = await _seed_org(db_session)
        integ_h = await _seed_integration(db_session, f"del-h-{tag}")
        integ_l = await _seed_integration(db_session, f"del-l-{tag}")
        await _seed_mapping(db_session, integ_h, org_h.id, entity_id="ent-h")
        await _seed_mapping(db_session, integ_l, org_l.id, entity_id="ent-l")
        user_h = _StubUser(organization_id=org_h.id)
        principal_l = _principal(org_l.id)

        http_value = await _http_delete(
            db_session, user=user_h, name=integ_h.name, scope=str(org_h.id)
        )
        local = await _local_delete(
            db_session, principal=principal_l, name=integ_l.name,
            scope=str(org_l.id),
        )
        assert http_value == {"deleted": True}
        assert local["ok"] is True
        assert local["result"] == {"deleted": True}

        from src.repositories.integrations import IntegrationsRepository

        repo = IntegrationsRepository(db_session)
        assert await repo.get_mapping_by_org(integ_l.id, org_l.id) is None

        # Second delete is a null, not an error, on both paths.
        http_again = await _http_delete(
            db_session, user=user_h, name=integ_h.name, scope=str(org_h.id)
        )
        local_again = await _local_delete(
            db_session, principal=principal_l, name=integ_l.name,
            scope=str(org_l.id),
        )
        assert http_again == {"deleted": False}
        assert local_again["ok"] is True
        assert local_again["result"] == {"deleted": False}

    async def test_missing_integration_is_false(self, db_session):
        org = await _seed_org(db_session)
        user = _StubUser(organization_id=org.id)
        principal = _principal(org.id)
        http_value = await _http_delete(
            db_session, user=user, name="missing", scope=str(org.id)
        )
        local = await _local_delete(
            db_session, principal=principal, name="missing",
            scope=str(org.id),
        )
        assert http_value == {"deleted": False}
        assert local["ok"] is True
        assert local["result"] == {"deleted": False}

    async def test_global_scope_is_false(self, db_session):
        tag = uuid4().hex[:8]
        integration = await _seed_integration(db_session, f"del-glob-{tag}")
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        http_value = await _http_delete(
            db_session, user=user, name=integration.name, scope="global"
        )
        local = await _local_delete(
            db_session, principal=principal, name=integration.name,
            scope="global",
        )
        assert http_value == {"deleted": False}
        assert local["ok"] is True
        assert local["result"] == {"deleted": False}

    async def test_cross_org_denied(self, db_session):
        import fastapi

        tag = uuid4().hex[:8]
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"del-denied-{tag}")
        await _seed_mapping(db_session, integration, org_b.id, entity_id="ent-b")
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_delete(
                db_session, user=user, name=integration.name,
                scope=str(org_b.id),
            )
        assert exc_info.value.status_code == 403
        local = await _local_delete(
            db_session, principal=principal, name=integration.name,
            scope=str(org_b.id),
        )
        assert local["ok"] is False
        assert local["status"] == 403

        # The other org's row survived the denied attempt.
        from src.repositories.integrations import IntegrationsRepository

        repo = IntegrationsRepository(db_session)
        assert await repo.get_mapping_by_org(integration.id, org_b.id) is not None


@pytest.mark.asyncio
class TestRefreshParity:
    async def test_client_credentials_success_persists(self, db_session):
        from src.core.security import decrypt_secret
        from src.models.orm.oauth import OAuthToken
        from sqlalchemy import select

        tag = uuid4().hex[:8]
        org_h = await _seed_org(db_session)
        org_l = await _seed_org(db_session)
        integ_h = await _seed_integration(db_session, f"ref-h-{tag}")
        integ_l = await _seed_integration(db_session, f"ref-l-{tag}")
        prov_h = await _seed_provider(db_session, integ_h)
        prov_l = await _seed_provider(db_session, integ_l)
        user_h = _StubUser(organization_id=org_h.id)
        principal_l = _principal(org_l.id)

        with patch(
            "src.services.oauth_provider.refresh_oauth_token_http",
            new=AsyncMock(return_value=_refresh_outcome()),
        ):
            http_value = await _http_refresh(
                db_session, user=user_h, connection_name=prov_h.provider_name
            )
            local = await _local_refresh(
                db_session, principal=principal_l,
                connection_name=prov_l.provider_name,
            )
        assert http_value.access_token == "new-access"
        assert http_value.expires_at is not None
        assert local["ok"] is True, local
        assert local["result"] == {
            "access_token": "new-access",
            "expires_at": http_value.expires_at,
        }
        # Committed state: a token row now holds the fresh secret, and the
        # provider is marked completed — on the local path too.
        rows = (
            await db_session.execute(
                select(OAuthToken).where(OAuthToken.provider_id == prov_l.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        raw = rows[0].encrypted_access_token
        assert decrypt_secret(raw.decode() if isinstance(raw, bytes) else raw) == "new-access"
        await db_session.refresh(prov_l)
        assert prov_l.status == "completed"
        assert prov_l.last_token_refresh is not None

    async def test_auth_code_rotates_with_row_lock(self, db_session):
        from src.core.security import decrypt_secret
        from src.repositories.oauth import OAuthTokenRepository

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"ref-ac-{tag}")
        provider = await _seed_provider(
            db_session, integration, flow="authorization_code"
        )
        await _seed_token(db_session, provider, org_id=org.id)
        user = _StubUser(organization_id=org.id)
        principal = _principal(org.id)

        lock_calls: list[bool] = []
        orig = OAuthTokenRepository.get_org_level_for_provider

        async def _spy(self, provider_id, *, for_update=False):
            lock_calls.append(for_update)
            return await orig(self, provider_id, for_update=for_update)

        with (
            patch.object(
                OAuthTokenRepository, "get_org_level_for_provider", _spy
            ),
            patch(
                "src.services.oauth_provider.refresh_oauth_token_http",
                new=AsyncMock(return_value=_refresh_outcome()),
            ),
        ):
            http_value = await _http_refresh(
                db_session, user=user, connection_name=provider.provider_name
            )
            local = await _local_refresh(
                db_session, principal=principal,
                connection_name=provider.provider_name,
            )
        assert lock_calls and all(lock_calls)
        assert http_value.access_token == "new-access"
        assert local["ok"] is True, local
        assert local["result"]["access_token"] == "new-access"

        from sqlalchemy import select
        from src.models.orm.oauth import OAuthToken

        rows = (
            await db_session.execute(
                select(OAuthToken).where(
                    OAuthToken.provider_id == provider.id,
                    OAuthToken.organization_id == org.id,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        raw = rows[0].encrypted_refresh_token
        assert decrypt_secret(raw.decode() if isinstance(raw, bytes) else raw) == "new-refresh"

    async def test_missing_provider_is_404(self, db_session):
        import fastapi

        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_refresh(
                db_session, user=user, connection_name="no-such-provider",
                scope="global",
            )
        assert exc_info.value.status_code == 404
        local = await _local_refresh(
            db_session, principal=principal,
            connection_name="no-such-provider", scope="global",
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_missing_refresh_token_is_400(self, db_session):
        import fastapi

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"ref-400-{tag}")
        provider = await _seed_provider(
            db_session, integration, flow="authorization_code"
        )
        user = _StubUser(organization_id=org.id)
        principal = _principal(org.id)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_refresh(
                db_session, user=user, connection_name=provider.provider_name
            )
        assert exc_info.value.status_code == 400
        local = await _local_refresh(
            db_session, principal=principal,
            connection_name=provider.provider_name,
        )
        assert local["ok"] is False
        assert local["status"] == 400

    async def test_provider_failure_is_502(self, db_session):
        import fastapi

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"ref-502-{tag}")
        provider = await _seed_provider(db_session, integration)
        user = _StubUser(organization_id=org.id)
        principal = _principal(org.id)
        with patch(
            "src.services.oauth_provider.refresh_oauth_token_http",
            new=AsyncMock(
                return_value={"success": False, "error": "provider down"}
            ),
        ):
            with pytest.raises(fastapi.HTTPException) as exc_info:
                await _http_refresh(
                    db_session, user=user,
                    connection_name=provider.provider_name,
                )
            local = await _local_refresh(
                db_session, principal=principal,
                connection_name=provider.provider_name,
            )
        assert exc_info.value.status_code == 502
        assert local["ok"] is False
        assert local["status"] == 502

    async def test_cross_org_denied(self, db_session):
        import fastapi

        tag = uuid4().hex[:8]
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"ref-denied-{tag}")
        provider = await _seed_provider(db_session, integration)
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_refresh(
                db_session, user=user,
                connection_name=provider.provider_name, scope=str(org_b.id),
            )
        assert exc_info.value.status_code == 403
        local = await _local_refresh(
            db_session, principal=principal,
            connection_name=provider.provider_name, scope=str(org_b.id),
        )
        assert local["ok"] is False
        assert local["status"] == 403

    @pytest.mark.parametrize("flow", ["client_credentials", "authorization_code"])
    async def test_external_cannot_use_global_provider(self, db_session, flow):
        import fastapi

        # A global provider's client secret must not reach token refresh,
        # even for client_credentials (which needs no stored refresh token).
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"ref-ext-{tag}")
        provider = await _seed_provider(db_session, integration, flow=flow)
        await _seed_token(
            db_session, provider, org_id=None,
            access="global-access-canary", refresh="global-refresh-canary",
        )
        user = _StubUser(organization_id=org.id, is_external=True)
        principal = _principal(org.id, is_external=True)
        with patch(
            "src.services.oauth_provider.refresh_oauth_token_http", new_callable=AsyncMock
        ) as refresh:
            with pytest.raises(fastapi.HTTPException) as exc_info:
                await _http_refresh(
                    db_session, user=user, connection_name=provider.provider_name
                )
            assert exc_info.value.status_code == 404
            local = await _local_refresh(
                db_session, principal=principal,
                connection_name=provider.provider_name,
            )
            refresh.assert_not_awaited()
        assert local["ok"] is False
        assert local["status"] == 404
        assert "global-refresh-canary" not in local["detail"]

    async def test_org_only_provider_lookup_never_cascades(self, db_session):
        from src.repositories.oauth import OAuthProviderRepository

        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, f"lookup-{uuid4().hex[:8]}")
        global_provider = await _seed_provider(db_session, integration)
        repo = OAuthProviderRepository(
            db_session, org_id=org.id, is_superuser=False, is_external=True
        )
        assert await repo.get_org_level_by_provider_name(
            global_provider.provider_name
        ) is None

        own_provider = await _seed_provider(db_session, None, org_id=org.id)
        assert await repo.get_org_level_by_provider_name(
            own_provider.provider_name
        ) == own_provider


class TestChildTransportMutations:
    async def _pump(self, req_conn, resp_conn, handler, count=1):
        for _ in range(count):
            raw = await asyncio.to_thread(req_conn.recv_bytes, 65537)
            frame = json.loads(raw.decode("utf-8"))
            response = handler(frame)
            await asyncio.to_thread(resp_conn.send_bytes, json.dumps(response).encode())

    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    @pytest.mark.asyncio
    async def test_upsert_and_delete_round_trip_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        item = {
            "id": "mid", "integration_id": "iid", "organization_id": "oid",
            "entity_id": "ent-1", "entity_name": None, "oauth_token_id": None,
            "config": {"region": "eu"},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        handlers = {
            "integrations.upsert_mapping": lambda f: {
                "v": 1, "id": f["id"], "ok": True, "result": item,
            },
            "integrations.delete_mapping": lambda f: {
                "v": 1, "id": f["id"], "ok": True,
                "result": {"deleted": True},
            },
        }

        async def _pump_ops():
            for _ in range(len(handlers)):
                raw = await asyncio.to_thread(req_recv.recv_bytes, 65537)
                frame = json.loads(raw.decode("utf-8"))
                assert "solution" not in frame, frame
                response = handlers[frame["op"]](frame)
                await asyncio.to_thread(resp_send.send_bytes, json.dumps(response).encode())

        pump = asyncio.create_task(_pump_ops())
        try:
            from bifrost.integrations import integrations

            with patch("bifrost.integrations.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                mapping = await integrations.upsert_mapping(
                    "P", scope="oid", entity_id="ent-1",
                    config={"region": "eu"},
                )
                assert mapping.entity_id == "ent-1"
                assert mapping.config == {"region": "eu"}
                assert await integrations.delete_mapping("P", scope="oid") is True
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_upsert_local_error_matches_http_runtime_error(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {"v": 1, "id": f["id"], "ok": False,
                           "status": 404, "detail": "Integration 'P' not found"},
            )
        )
        try:
            from bifrost.integrations import integrations

            with patch("bifrost.integrations.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                with pytest.raises(RuntimeError, match="Failed to upsert mapping"):
                    await integrations.upsert_mapping(
                        "P", scope="oid", entity_id="ent-1"
                    )
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_refresh_round_trip_registers_secret_without_http(self):
        from bifrost import _local_transport as lt
        from bifrost._context import (
            clear_execution_context,
            set_execution_context,
        )
        from src.sdk.context import ExecutionContext

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="T", scope="org-1",
            organization=None, is_platform_admin=False,
            is_function_key=False, execution_id="exec-1",
        )
        set_execution_context(ctx)
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {"v": 1, "id": f["id"], "ok": True,
                           "result": {"access_token": "fresh-tok-live",
                                      "expires_at": None}},
            )
        )
        try:
            from bifrost.models import OAuthCredentials

            creds = OAuthCredentials(
                connection_name="P", client_id="cid", client_secret=None,
                authorization_url=None, token_url=None, scopes=[],
                access_token="stale", refresh_token="ref",
                expires_at=None,
            )
            with patch("bifrost.client.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                out = await creds.refresh()
            assert out is creds
            assert creds.access_token == "fresh-tok-live"
            assert "fresh-tok-live" in ctx._collect_secret_values()
            await pump
        finally:
            lt.clear()
            clear_execution_context()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_refresh_local_error_matches_http_runtime_error(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {"v": 1, "id": f["id"], "ok": False,
                           "status": 502, "detail": "provider down"},
                count=1,
            )
        )
        try:
            from bifrost.models import OAuthCredentials

            creds = OAuthCredentials(
                connection_name="P", client_id="cid", client_secret=None,
                authorization_url=None, token_url=None, scopes=[],
                access_token="stale", refresh_token="ref",
                expires_at=None,
            )
            with patch("bifrost.client.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                with pytest.raises(RuntimeError, match="Token refresh failed"):
                    await creds.refresh()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
