"""Stage 2b: HTTP/local parity for ``integrations.get/list_mappings/get_mapping``.

Covers the acceptance surface that does not need a forked child:

- the shared service (``shared.sdk_integrations``) resolves scope/cascade,
  declared-Solution 424, external-user behavior, merged configs, OAuth
  token cascade/decryption, and missing-entity nulls exactly like the HTTP
  handler (the handler delegates to it);
- the parent dispatcher calls that same service with a parent-derived
  principal (including the trusted Solution id) and maps scope/declared
  failures to HTTP-style statuses;
- the child transport performs real integrations round trips over a
  dedicated channel with zero HTTP requests, secret registration, error
  mapping without HTTP fallback, and missing-entity nulls.
"""

import asyncio
import contextlib
import json
import multiprocessing
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.models.contracts.cli import (
    SDKIntegrationsGetMappingRequest,
    SDKIntegrationsGetRequest,
    SDKIntegrationsListMappingsRequest,
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
        name=f"sdk-integ-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-integ-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_integration(
    db_session,
    name,
    *,
    secret_schema_key=None,
    defaults=None,
    secret_defaults=None,
):
    """Seed an integration with optional schema + global defaults.

    Returns the integration row. ``defaults`` are plain config defaults;
    ``secret_defaults`` are SECRET-typed (stored encrypted).
    """
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm import Config as ConfigModel
    from src.models.orm.integrations import (
        Integration as IntegrationModel,
        IntegrationConfigSchema as SchemaModel,
    )

    integration = IntegrationModel(name=name)
    db_session.add(integration)
    await db_session.flush()

    if secret_schema_key:
        db_session.add(
            SchemaModel(
                integration_id=integration.id,
                key=secret_schema_key,
                type="secret",
                position=0,
            )
        )
        await db_session.flush()

    for key, value in (defaults or {}).items():
        db_session.add(
            ConfigModel(
                key=key,
                value={"value": value},
                config_type=ConfigTypeEnum.STRING,
                organization_id=None,
                integration_id=integration.id,
                updated_by="sdk-integ-test",
            )
        )
    if secret_defaults:
        from src.core.security import encrypt_secret

        for key, value in secret_defaults.items():
            db_session.add(
                ConfigModel(
                    key=key,
                    value={"value": encrypt_secret(value)},
                    config_type=ConfigTypeEnum.SECRET,
                    organization_id=None,
                    integration_id=integration.id,
                    updated_by="sdk-integ-test",
                )
            )
    await db_session.flush()
    return integration


async def _seed_mapping(db_session, integration, org_id, *, entity_id, overrides=None):
    """Seed an org mapping with optional org config overrides (plain)."""
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm import Config as ConfigModel
    from src.models.orm.integrations import IntegrationMapping as MappingModel

    mapping = MappingModel(
        integration_id=integration.id,
        organization_id=org_id,
        entity_id=entity_id,
        entity_name=f"entity-{entity_id[:8]}",
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
                updated_by="sdk-integ-test",
            )
        )
    await db_session.flush()
    return mapping


async def _seed_provider_with_token(
    db_session, integration, *, org_id=None, access_token="stored-access", flow="authorization_code"
):
    """Seed an OAuth provider + user_id=NULL token (org-scoped or global)."""
    from src.core.security import encrypt_secret
    from src.models.orm.oauth import OAuthProvider, OAuthToken

    provider = OAuthProvider(
        provider_name=f"prov-{uuid4().hex[:8]}",
        client_id="test-client",
        encrypted_client_secret=encrypt_secret("test-client-secret").encode(),
        oauth_flow_type=flow,
        token_url="https://example.com/token",
        token_url_defaults={},
        scopes=["read"],
        integration_id=integration.id,
        organization_id=None,
    )
    db_session.add(provider)
    await db_session.flush()
    token = OAuthToken(
        organization_id=org_id,
        provider_id=provider.id,
        user_id=None,
        encrypted_access_token=encrypt_secret(access_token).encode(),
        scopes=["read"],
    )
    db_session.add(token)
    await db_session.flush()
    return provider, token


async def _http_get(db_session, *, name, scope, oauth_scope, solution, user):
    from src.routers.cli import sdk_integrations_get

    return await sdk_integrations_get(
        SDKIntegrationsGetRequest(
            name=name, scope=scope, oauth_scope=oauth_scope, solution=solution
        ),
        user,
        db_session,
    )


async def _local_get(db_session, *, name, scope, oauth_scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame = {
        "v": 1,
        "id": "parity-1",
        "op": "integrations.get",
        "name": name,
        "scope": scope,
        "oauth_scope": oauth_scope,
    }
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


async def _http_list(db_session, *, name, scope, user):
    from src.routers.cli import sdk_integrations_list_mappings

    return await sdk_integrations_list_mappings(
        SDKIntegrationsListMappingsRequest(name=name, scope=scope),
        user,
        db_session,
    )


async def _local_list(db_session, *, name, scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frames

    frame = {
        "v": 1,
        "id": "parity-list-1",
        "op": "integrations.list_mappings",
        "name": name,
        "scope": scope,
    }
    frames = await dispatch_frames(
        lambda: _db_factory(db_session), principal, frame
    )
    collected = list(frames)
    first = collected[0]
    if first.get("chunked"):
        import base64

        assert len(collected) == first["parts"] + 1
        raw = b"".join(base64.b64decode(part["data"]) for part in collected[1:])
        assert len(raw) == first["total"]
        return {"ok": True, "result": json.loads(raw)}
    return first


async def _http_get_mapping(db_session, *, name, scope, entity_id, user):
    from src.routers.cli import sdk_integrations_get_mapping

    return await sdk_integrations_get_mapping(
        SDKIntegrationsGetMappingRequest(
            name=name, scope=scope, entity_id=entity_id
        ),
        user,
        db_session,
    )


async def _local_get_mapping(db_session, *, name, scope, entity_id, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame = {
        "v": 1,
        "id": "parity-gm-1",
        "op": "integrations.get_mapping",
        "name": name,
        "scope": scope,
        "entity_id": entity_id,
    }
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


@pytest.mark.asyncio
class TestGetParity:
    async def test_mapping_path_merged_config_and_oauth(self, db_session):
        tag = uuid4().hex[:8]
        name = f"integ-{tag}"
        org = await _seed_org(db_session)
        integration = await _seed_integration(
            db_session,
            name,
            secret_schema_key="api_key",
            defaults={"region": "us"},
            secret_defaults={"api_key": "global-secret"},
        )
        await _seed_mapping(
            db_session,
            integration,
            org.id,
            entity_id="tenant-1",
            overrides={"region": "eu"},
        )
        await _seed_provider_with_token(
            db_session, integration, org_id=None, access_token="tok-abc"
        )
        user = _StubUser(organization_id=org.id)
        principal = _principal(org.id)
        http_value = await _http_get(
            db_session, name=name, scope=None, oauth_scope=None,
            solution=None, user=user,
        )
        local = await _local_get(
            db_session, name=name, scope=None, oauth_scope=None,
            principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump()
        assert local["result"]["entity_id"] == "tenant-1"
        # Org override wins over the global default.
        assert local["result"]["config"]["region"] == "eu"
        # SECRET default decrypted on both paths.
        assert local["result"]["config"]["api_key"] == "global-secret"
        assert local["result"]["config_secret_keys"] == ["api_key"]
        # Global token cascades to the org caller on both paths.
        assert local["result"]["oauth"]["access_token"] == "tok-abc"
        assert local["result"]["oauth"]["client_secret"] == "test-client-secret"

    async def test_defaults_path_without_mapping(self, db_session):
        tag = uuid4().hex[:8]
        name = f"integ-def-{tag}"
        org = await _seed_org(db_session)
        await _seed_integration(
            db_session, name, defaults={"region": "us"}
        )
        user = _StubUser(organization_id=org.id)
        principal = _principal(org.id)
        http_value = await _http_get(
            db_session, name=name, scope=None, oauth_scope=None,
            solution=None, user=user,
        )
        local = await _local_get(
            db_session, name=name, scope=None, oauth_scope=None,
            principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump()
        assert local["result"]["entity_name"] is None
        assert local["result"]["config"] == {"region": "us"}

    async def test_missing_is_null_not_error(self, db_session):
        name = f"integ-missing-{uuid4().hex[:8]}"
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        http_value = await _http_get(
            db_session, name=name, scope="global", oauth_scope=None,
            solution=None, user=user,
        )
        local = await _local_get(
            db_session, name=name, scope="global", oauth_scope=None,
            principal=principal,
        )
        assert http_value is None
        assert local["ok"] is True
        assert local["result"] is None

    async def test_declared_solution_is_424(self, db_session):
        from fastapi import HTTPException

        from src.models.orm.solution_connection_schema import (
            SolutionConnectionSchema as ConnSchema,
        )
        from src.models.orm.solutions import Solution as SolutionModel

        tag = uuid4().hex[:8]
        name = f"integ-declared-{tag}"
        solution = SolutionModel(slug=f"sol-{tag}", name=f"Solution {tag}")
        db_session.add(solution)
        await db_session.flush()
        db_session.add(
            ConnSchema(solution_id=solution.id, integration_name=name)
        )
        await db_session.flush()

        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True, solution_id=solution.id)
        with pytest.raises(HTTPException) as exc_info:
            await _http_get(
                db_session, name=name, scope="global", oauth_scope=None,
                solution=str(solution.id), user=user,
            )
        assert exc_info.value.status_code == 424
        local = await _local_get(
            db_session, name=name, scope="global", oauth_scope=None,
            principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 424
        assert name in local["detail"]

    async def test_undeclared_solution_stays_null(self, db_session):
        name = f"integ-loose-{uuid4().hex[:8]}"
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        http_value = await _http_get(
            db_session, name=name, scope="global", oauth_scope=None,
            solution=None, user=user,
        )
        local = await _local_get(
            db_session, name=name, scope="global", oauth_scope=None,
            principal=principal,
        )
        assert http_value is None
        assert local["ok"] is True
        assert local["result"] is None

    async def test_denied_cross_org_scope(self, db_session):
        import fastapi

        tag = uuid4().hex[:8]
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        await _seed_integration(db_session, f"integ-d-{tag}")
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_get(
                db_session, name=f"integ-d-{tag}", scope=str(org_b.id),
                oauth_scope=None, solution=None, user=user,
            )
        assert exc_info.value.status_code == 403
        local = await _local_get(
            db_session, name=f"integ-d-{tag}", scope=str(org_b.id),
            oauth_scope=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 403

    async def test_malformed_scope_is_422(self, db_session):
        org = await _seed_org(db_session)
        principal = _principal(org.id)
        local = await _local_get(
            db_session, name="x", scope="not-a-uuid",
            oauth_scope=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_malformed_name_is_422(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _principal(is_platform_admin=True)
        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {"v": 1, "id": "bad-name", "op": "integrations.get",
             "name": {"nested": 1}, "scope": "global",
             "oauth_scope": None},
        )
        assert response["ok"] is False
        assert response["status"] == 422

    async def test_external_drops_global_tier(self, db_session):
        tag = uuid4().hex[:8]
        name = f"integ-ext-{tag}"
        org = await _seed_org(db_session)
        integration = await _seed_integration(
            db_session, name, secret_defaults={"api_key": "global-secret-canary"}
        )
        await _seed_mapping(
            db_session, integration, org.id, entity_id="tenant-ext"
        )
        await _seed_provider_with_token(
            db_session, integration, org_id=None, access_token="global-token"
        )
        user = _StubUser(organization_id=org.id, is_external=True)
        principal = _principal(org.id, is_external=True)
        http_value = await _http_get(
            db_session, name=name, scope=None, oauth_scope=None,
            solution=None, user=user,
        )
        local = await _local_get(
            db_session, name=name, scope=None, oauth_scope=None,
            principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump()
        blob = json.dumps(local["result"], default=str)
        assert "global-secret-canary" not in blob
        assert "global-token" not in blob
        assert local["result"]["oauth"]["client_secret"] is None


@pytest.mark.asyncio
class TestListMappingsParity:
    async def _seed_two_orgs(self, db_session, name):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        integration = await _seed_integration(db_session, name)
        await _seed_mapping(db_session, integration, org_a.id, entity_id="ent-a")
        await _seed_mapping(db_session, integration, org_b.id, entity_id="ent-b")
        return org_a, org_b

    async def test_missing_integration_is_null(self, db_session):
        name = f"integ-nolist-{uuid4().hex[:8]}"
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        assert await _http_list(db_session, name=name, scope="global", user=user) is None
        local = await _local_list(db_session, name=name, scope="global", principal=principal)
        assert local["ok"] is True
        assert local["result"] is None

    async def test_own_org_only_for_plain_caller(self, db_session):
        name = f"integ-list-{uuid4().hex[:8]}"
        org_a, _org_b = await self._seed_two_orgs(db_session, name)
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        http_value = await _http_list(db_session, name=name, scope=None, user=user)
        local = await _local_list(db_session, name=name, scope=None, principal=principal)
        assert local["ok"] is True, local
        assert local["result"] == {"items": [i.model_dump() for i in http_value.items]}
        assert [i["entity_id"] for i in local["result"]["items"]] == ["ent-a"]

    async def test_provider_org_lists_all(self, db_session):
        name = f"integ-prov-{uuid4().hex[:8]}"
        provider_org = await _seed_org(db_session, is_provider=True)
        other = await _seed_org(db_session)
        integration = await _seed_integration(db_session, name)
        await _seed_mapping(db_session, integration, provider_org.id, entity_id="ent-p")
        await _seed_mapping(db_session, integration, other.id, entity_id="ent-o")
        user = _StubUser(organization_id=provider_org.id)
        principal = _principal(provider_org.id, is_provider_org=True)
        http_value = await _http_list(db_session, name=name, scope=None, user=user)
        local = await _local_list(db_session, name=name, scope=None, principal=principal)
        assert local["ok"] is True, local
        assert local["result"] == {"items": [i.model_dump() for i in http_value.items]}
        assert sorted(i["entity_id"] for i in local["result"]["items"]) == ["ent-o", "ent-p"]

    async def test_global_scope_lists_all_for_admin(self, db_session):
        name = f"integ-glob-{uuid4().hex[:8]}"
        await self._seed_two_orgs(db_session, name)
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        http_value = await _http_list(db_session, name=name, scope="global", user=user)
        local = await _local_list(db_session, name=name, scope="global", principal=principal)
        assert local["ok"] is True, local
        assert len(local["result"]["items"]) == 2
        assert local["result"] == {"items": [i.model_dump() for i in http_value.items]}

    async def test_scopeless_caller_gets_empty(self, db_session):
        name = f"integ-empty-{uuid4().hex[:8]}"
        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, name)
        await _seed_mapping(db_session, integration, org.id, entity_id="ent-x")
        user = _StubUser()
        principal = _principal()
        http_value = await _http_list(db_session, name=name, scope=None, user=user)
        local = await _local_list(db_session, name=name, scope=None, principal=principal)
        assert http_value.items == []
        assert local["ok"] is True
        assert local["result"] == {"items": []}

    async def test_cross_org_denied(self, db_session):
        import fastapi

        name = f"integ-listd-{uuid4().hex[:8]}"
        org_a, org_b = await self._seed_two_orgs(db_session, name)
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_list(db_session, name=name, scope=str(org_b.id), user=user)
        assert exc_info.value.status_code == 403
        local = await _local_list(
            db_session, name=name, scope=str(org_b.id), principal=principal
        )
        assert local["ok"] is False
        assert local["status"] == 403


@pytest.mark.asyncio
class TestGetMappingParity:
    async def test_by_org_scope(self, db_session):
        tag = uuid4().hex[:8]
        name = f"integ-gm-{tag}"
        org = await _seed_org(db_session)
        integration = await _seed_integration(db_session, name)
        await _seed_mapping(db_session, integration, org.id, entity_id="tenant-gm")
        user = _StubUser(organization_id=org.id)
        principal = _principal(org.id)
        http_value = await _http_get_mapping(
            db_session, name=name, scope=None, entity_id=None, user=user
        )
        local = await _local_get_mapping(
            db_session, name=name, scope=None, entity_id=None, principal=principal
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump()
        assert local["result"]["entity_id"] == "tenant-gm"

    async def test_by_entity_id_within_own_org(self, db_session):
        tag = uuid4().hex[:8]
        name = f"integ-gme-{tag}"
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        integration = await _seed_integration(db_session, name)
        await _seed_mapping(db_session, integration, org_a.id, entity_id="ent-aaa")
        await _seed_mapping(db_session, integration, org_b.id, entity_id="ent-bbb")
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        http_value = await _http_get_mapping(
            db_session, name=name, scope=None, entity_id="ent-aaa", user=user
        )
        local = await _local_get_mapping(
            db_session, name=name, scope=None, entity_id="ent-aaa", principal=principal
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump()
        assert local["result"]["entity_id"] == "ent-aaa"

    async def test_entity_probe_cannot_reach_other_org(self, db_session):
        tag = uuid4().hex[:8]
        name = f"integ-gmp-{tag}"
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        integration = await _seed_integration(db_session, name)
        await _seed_mapping(db_session, integration, org_b.id, entity_id="ent-secret")
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        http_value = await _http_get_mapping(
            db_session, name=name, scope=None, entity_id="ent-secret", user=user
        )
        local = await _local_get_mapping(
            db_session, name=name, scope=None, entity_id="ent-secret", principal=principal
        )
        assert http_value is None
        assert local["ok"] is True
        assert local["result"] is None

    async def test_global_bypass_searches_all_by_entity(self, db_session):
        tag = uuid4().hex[:8]
        name = f"integ-gmg-{tag}"
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        integration = await _seed_integration(db_session, name)
        await _seed_mapping(db_session, integration, org_b.id, entity_id="ent-wide")
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        http_value = await _http_get_mapping(
            db_session, name=name, scope="global", entity_id="ent-wide", user=user
        )
        local = await _local_get_mapping(
            db_session, name=name, scope="global", entity_id="ent-wide",
            principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump()
        assert local["result"]["entity_id"] == "ent-wide"
        assert local["result"]["organization_id"] == str(org_b.id)
        assert org_a.id is not None

    async def test_missing_mapping_is_null(self, db_session):
        name = f"integ-gmm-{uuid4().hex[:8]}"
        await _seed_integration(db_session, name)
        org = await _seed_org(db_session)
        user = _StubUser(organization_id=org.id)
        principal = _principal(org.id)
        http_value = await _http_get_mapping(
            db_session, name=name, scope=None, entity_id=None, user=user
        )
        local = await _local_get_mapping(
            db_session, name=name, scope=None, entity_id=None, principal=principal
        )
        assert http_value is None
        assert local["ok"] is True
        assert local["result"] is None

    async def test_cross_org_denied(self, db_session):
        import fastapi

        name = f"integ-gmd-{uuid4().hex[:8]}"
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        await _seed_integration(db_session, name)
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_get_mapping(
                db_session, name=name, scope=str(org_b.id),
                entity_id=None, user=user,
            )
        assert exc_info.value.status_code == 403
        local = await _local_get_mapping(
            db_session, name=name, scope=str(org_b.id),
            entity_id=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 403


class TestChildTransport:
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
    async def test_get_round_trip_registers_secrets_without_http(self):
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
        payload = {
            "integration_id": "iid",
            "entity_id": "tenant-1",
            "entity_name": None,
            "config": {"api_key": "shh-value"},
            "oauth": {
                "connection_name": "P",
                "client_id": "cid",
                "client_secret": "csec",
                "authorization_url": None,
                "token_url": None,
                "scopes": [],
                "access_token": "tok-live",
                "refresh_token": "ref-live",
                "expires_at": None,
            },
            "config_secret_keys": ["api_key"],
        }
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {"v": 1, "id": f["id"], "ok": True, "result": payload},
            )
        )
        try:
            from bifrost.integrations import integrations

            with patch("bifrost.integrations.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                data = await integrations.get("P")
            assert data.entity_id == "tenant-1"
            assert data.oauth.access_token == "tok-live"
            secrets = ctx._collect_secret_values()
            assert "tok-live" in secrets
            assert "ref-live" in secrets
            assert "csec" in secrets
            assert "shh-value" in secrets
            # The Solution id is parent-derived: the child never sends it.
            await pump
        finally:
            lt.clear()
            clear_execution_context()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_list_and_get_mapping_round_trip_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        item = {
            "id": "mid", "integration_id": "iid", "organization_id": "oid",
            "entity_id": "ent-1", "entity_name": None, "oauth_token_id": None,
            "config": {}, "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        handlers = {
            "integrations.list_mappings": lambda f: {
                "v": 1, "id": f["id"], "ok": True,
                "result": {"items": [item]},
            },
            "integrations.get_mapping": lambda f: {
                "v": 1, "id": f["id"], "ok": True, "result": item,
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
                mappings = await integrations.list_mappings("P")
                assert mappings is not None
                assert [m.entity_id for m in mappings] == ["ent-1"]
                mapping = await integrations.get_mapping("P")
                assert mapping is not None
                assert mapping.entity_id == "ent-1"
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_missing_returns_none_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {"v": 1, "id": f["id"], "ok": True, "result": None},
                count=3,
            )
        )
        try:
            from bifrost.integrations import integrations

            with patch("bifrost.integrations.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                assert await integrations.get("Missing") is None
                assert await integrations.list_mappings("Missing") is None
                assert await integrations.get_mapping("Missing") is None
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_local_error_never_falls_back_to_http(self):
        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAPIError, BifrostAuthorizationError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)

        seen = []

        async def _pump_statuses():
            for status in (403, 424):
                raw = await asyncio.to_thread(req_recv.recv_bytes, 65537)
                frame = json.loads(raw.decode("utf-8"))
                seen.append(frame["op"])
                await asyncio.to_thread(
                    resp_send.send_bytes,
                    json.dumps(
                        {"v": 1, "id": frame["id"], "ok": False,
                         "status": status, "detail": "denied"}
                    ).encode(),
                )

        pump = asyncio.create_task(_pump_statuses())
        try:
            from bifrost.integrations import integrations

            with patch("bifrost.integrations.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                with pytest.raises(BifrostAuthorizationError):
                    await integrations.get("P")
                with pytest.raises(BifrostAPIError):
                    await integrations.get("P")
            await pump
            assert seen == ["integrations.get", "integrations.get"]
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
