"""OPEN-E / NEW-G e2e: the WHOLE /api/sdk/integrations/* surface must not leak
the GLOBAL tier to external users.

This is the coverage gap that let OPEN-E + NEW-G ship past FOUR adversarial
passes: OPEN-E's test only hit get + refresh_token, so the three sibling
endpoints (list_mappings, get_mapping, upsert_mapping) — which ALSO merge the
global integration SECRET default into their config echo — slipped. This file
now parametrizes over EVERY config-returning SDK endpoint so a 5th sibling
can't hide.

We seed a GLOBAL integration with a decrypt-able SECRET config default + a
global OAuth token, AND an org-scoped mapping in the EXTERNAL user's own org
(so list/get/upsert_mapping return a mapping whose merged config would, pre-fix,
include the global SECRET default). Then assert:

  - an EXTERNAL portal user gets NONE of the global SECRET/token back from ANY
    of get / list_mappings / get_mapping / upsert_mapping / refresh_token;
  - a NORMAL org user (and the sentinel/engine path) still receives the global
    tier — proving the restriction is external-specific, not a blanket break.

Engine/sentinel path is unchanged (a workflow legitimately uses its org's
integrations including global defaults).
"""

from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e

SUFFIX = uuid4().hex[:8]

# Decrypt-able canaries — the SDK endpoint decrypts SECRETs before returning.
GLOBAL_CONFIG_SECRET = f"GLOBAL-INTEG-SECRET-{SUFFIX}"
GLOBAL_CLIENT_SECRET = f"GLOBAL-CLIENT-SECRET-{SUFFIX}"
GLOBAL_ACCESS_TOKEN = f"GLOBAL-ACCESS-TOKEN-{SUFFIX}"


@pytest.fixture(scope="module")
def portal_role(e2e_client, platform_admin):
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": f"E2E Integ Portal {SUFFIX}", "description": "open-e e2e"},
    )
    assert resp.status_code == 201, resp.text
    role = resp.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def external_user(e2e_client, platform_admin, org1, portal_role) -> E2EUser:
    """External (portal) user in org1 — their org has NO integration mapping,
    so the endpoint would fall through to the global tier pre-fix."""
    user = E2EUser(
        email=f"e2e-integ-ext-{SUFFIX}@gobifrost.dev",
        password="ExternalPass123!",
        name=f"E2E Integ External {SUFFIX}",
        organization_id=UUID(org1["id"]),
    )
    resp = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": org1["id"],
            "is_superuser": False,
            "is_external": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_external"] is True
    user.user_id = UUID(body["id"])

    assign = e2e_client.post(
        f"/api/roles/{portal_role['id']}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(user.user_id)]},
    )
    assert assign.status_code == 204, assign.text

    user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
    user.organization_id = UUID(org1["id"])
    return user


@pytest_asyncio.fixture
async def global_integration(e2e_client, platform_admin, db_session, org1):
    """A GLOBAL integration with: a global SECRET config default + a global
    OAuth provider (decrypt-able client_secret) + a global OAuth token
    (decrypt-able access_token), PLUS an org-scoped mapping in the external
    user's own org (org1).

    The org mapping is what lets the mapping-echo siblings (list_mappings /
    get_mapping / upsert_mapping) reach the merged config: pre-fix they merge
    the GLOBAL SECRET default into the org mapping's config, leaking it to an
    external user who legitimately owns the mapping. The mapping carries only a
    NON-secret override, so any SECRET in the response can only be the global
    default canary.

    Function-scoped with a per-call-unique name/provider so tests never collide
    on the integration name (soft-delete doesn't free it immediately).
    """
    from src.core.security import encrypt_secret
    from src.models.enums import ConfigType
    from src.models.orm import Config as ConfigModel
    from src.models.orm import OAuthProvider
    from src.models.orm.oauth import OAuthToken

    uniq = uuid4().hex[:8]
    name = f"ext-integ-{uniq}"
    provider_name = f"ext_integ_provider_{uniq}"
    create = e2e_client.post(
        "/api/integrations",
        headers=platform_admin.headers,
        json={"name": name},
    )
    assert create.status_code == 201, create.text
    integration_id = UUID(create.json()["id"])

    # Global SECRET config default (organization_id=NULL).
    db_session.add(
        ConfigModel(
            key=f"api_key_{uniq}",
            value={"value": encrypt_secret(GLOBAL_CONFIG_SECRET)},
            config_type=ConfigType.SECRET,
            organization_id=None,
            integration_id=integration_id,
            updated_by="e2e-seed",
        )
    )
    provider = OAuthProvider(
        provider_name=provider_name,
        display_name="Ext Integ Provider",
        oauth_flow_type="client_credentials",
        client_id="ext-integ-client",
        encrypted_client_secret=encrypt_secret(GLOBAL_CLIENT_SECRET).encode(),
        authorization_url="https://example.com/authorize",
        token_url="https://example.com/token",
        scopes=["read"],
        redirect_uri="/api/oauth/callback/ext_integ",
        integration_id=integration_id,
        organization_id=None,
    )
    db_session.add(provider)
    await db_session.flush()

    db_session.add(
        OAuthToken(
            organization_id=None,
            provider_id=provider.id,
            user_id=None,
            encrypted_access_token=encrypt_secret(GLOBAL_ACCESS_TOKEN).encode(),
            scopes=["read"],
        )
    )
    await db_session.commit()

    # Org-scoped mapping in the external user's own org (org1), with a
    # NON-secret override only — so the mapping-echo siblings must merge the
    # GLOBAL SECRET default to expose the canary (the NEW-G leak path).
    mapping_resp = e2e_client.post(
        f"/api/integrations/{integration_id}/mappings",
        headers=platform_admin.headers,
        json={
            "organization_id": org1["id"],
            "entity_id": f"ext-entity-{uniq}",
            "config": {"non_secret_key": "org-value"},
        },
    )
    assert mapping_resp.status_code == 201, mapping_resp.text

    yield {
        "name": name,
        "integration_id": str(integration_id),
        "provider_name": provider_name,
        "org_id": org1["id"],
    }

    e2e_client.delete(
        f"/api/integrations/{integration_id}", headers=platform_admin.headers
    )


def _serialize(payload) -> str:
    import json

    return json.dumps(payload, default=str)


class TestExternalIntegrationsGet:
    """OPEN-E: sdk_integrations_get must return NONE of the global tier to an
    external caller on default scope."""

    def _get(self, e2e_client, user, name):
        return e2e_client.post(
            "/api/sdk/integrations/get",
            headers=user.headers,
            json={"name": name},
        )

    def test_external_gets_no_global_secret_or_token(
        self, e2e_client, external_user, global_integration
    ):
        resp = self._get(e2e_client, external_user, global_integration["name"])
        # Either an empty/None body or a body with no global tier — never the
        # decrypted global secrets.
        assert resp.status_code == 200, resp.text
        blob = _serialize(resp.json())
        assert GLOBAL_CONFIG_SECRET not in blob, (
            "external user received the global integration SECRET config"
        )
        assert GLOBAL_CLIENT_SECRET not in blob, (
            "external user received the global OAuth client_secret"
        )
        assert GLOBAL_ACCESS_TOKEN not in blob, (
            "external user received the global OAuth access_token"
        )

    def test_normal_user_still_gets_global_tier(
        self, e2e_client, org1_user, global_integration
    ):
        resp = self._get(e2e_client, org1_user, global_integration["name"])
        assert resp.status_code == 200, resp.text
        blob = _serialize(resp.json())
        # A normal org user's default-scope read still unions the global tier:
        # the integration config default AND the global OAuth token surface.
        assert GLOBAL_CONFIG_SECRET in blob, (
            "normal org user must still receive the global integration default"
        )
        assert GLOBAL_ACCESS_TOKEN in blob, (
            "normal org user must still receive the global OAuth access_token"
        )


# =============================================================================
# NEW-G — the WHOLE config-returning surface, parametrized. Each of these
# /api/sdk/integrations/* endpoints echoes a mapping's merged config. An
# external user owns the org mapping, so pre-fix the global SECRET default
# merged in and leaked. This is the auditable proof the whole surface is gated.
# =============================================================================


def _call_endpoint(e2e_client, user, endpoint, integ):
    """Invoke one config-returning SDK endpoint as ``user`` for the seeded
    integration, returning the response. ``scope`` is the external user's own
    org (default-scope reads resolve there)."""
    name = integ["name"]
    if endpoint == "get":
        return e2e_client.post(
            "/api/sdk/integrations/get", headers=user.headers, json={"name": name}
        )
    if endpoint == "list_mappings":
        return e2e_client.post(
            "/api/sdk/integrations/list_mappings",
            headers=user.headers,
            json={"name": name},
        )
    if endpoint == "get_mapping":
        return e2e_client.post(
            "/api/sdk/integrations/get_mapping",
            headers=user.headers,
            json={"name": name},
        )
    if endpoint == "upsert_mapping":
        # Re-upsert the caller's OWN org mapping (scope = own org); the response
        # echoes merged config (the post-write echo — cli.py:1110, a NEW-G site).
        return e2e_client.post(
            "/api/sdk/integrations/upsert_mapping",
            headers=user.headers,
            json={
                "name": name,
                "scope": str(integ["org_id"]),
                "entity_id": "ext-entity-upsert",
                "config": {"non_secret_key": "org-value-2"},
            },
        )
    raise AssertionError(f"unknown endpoint {endpoint}")


_CONFIG_ENDPOINTS = ["get", "list_mappings", "get_mapping", "upsert_mapping"]


@pytest.mark.parametrize("endpoint", _CONFIG_ENDPOINTS)
class TestExternalIntegrationsSurface:
    """NEW-G: EVERY config-returning /api/sdk/integrations/* endpoint must drop
    the global SECRET default for an external caller, and keep it for a normal
    org user."""

    def test_external_never_sees_global_secret(
        self, e2e_client, external_user, global_integration, endpoint
    ):
        resp = _call_endpoint(e2e_client, external_user, endpoint, global_integration)
        assert resp.status_code in (200, 201), f"{endpoint}: {resp.status_code} {resp.text}"
        blob = _serialize(resp.json())
        assert GLOBAL_CONFIG_SECRET not in blob, (
            f"external user received the global SECRET default via {endpoint}"
        )
        assert GLOBAL_CLIENT_SECRET not in blob, (
            f"external user received the global client_secret via {endpoint}"
        )
        assert GLOBAL_ACCESS_TOKEN not in blob, (
            f"external user received the global access_token via {endpoint}"
        )

    def test_normal_user_sees_global_secret(
        self, e2e_client, org1_user, global_integration, endpoint
    ):
        resp = _call_endpoint(e2e_client, org1_user, endpoint, global_integration)
        assert resp.status_code in (200, 201), f"{endpoint}: {resp.status_code} {resp.text}"
        blob = _serialize(resp.json())
        # The mapping-echo siblings (and get) merge the global SECRET default
        # into the org mapping's config for a normal org user — proving the
        # restriction is external-specific, not a blanket break.
        assert GLOBAL_CONFIG_SECRET in blob, (
            f"normal org user must still receive the global SECRET default via {endpoint}"
        )


class TestExternalIntegrationsRefreshToken:
    """OPEN-E: sdk_integrations_refresh_token must not let an external refresh /
    receive the GLOBAL OAuth token."""

    def _refresh(self, e2e_client, user, provider_name):
        return e2e_client.post(
            "/api/sdk/integrations/refresh_token",
            headers=user.headers,
            json={"connection_name": provider_name},
        )

    def test_external_cannot_refresh_global_provider(
        self, e2e_client, external_user, global_integration
    ):
        provider_name = global_integration["provider_name"]
        resp = self._refresh(e2e_client, external_user, provider_name)
        # The provider is GLOBAL; an external's by-name cascade drops the global
        # tier → 404 (provider not found). It must NOT return a fresh global
        # access_token.
        assert resp.status_code in (400, 404, 502), (
            f"external must not reach the global provider: "
            f"{resp.status_code} {resp.text}"
        )
        assert GLOBAL_ACCESS_TOKEN not in resp.text
        assert GLOBAL_CLIENT_SECRET not in resp.text

    def test_normal_user_reaches_global_provider(
        self, e2e_client, org1_user, global_integration
    ):
        # A normal org user's by-name cascade DOES reach the global provider —
        # it then attempts a real HTTP refresh against example.com (which
        # fails with 502), proving the provider resolved (not a 404 denial).
        provider_name = global_integration["provider_name"]
        resp = self._refresh(e2e_client, org1_user, provider_name)
        assert resp.status_code != 404, (
            f"normal org user must reach the global provider (not 404): "
            f"{resp.status_code} {resp.text}"
        )
