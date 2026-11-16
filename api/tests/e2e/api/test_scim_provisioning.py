"""SCIM provisioning through a live API: an Entra/Okta-shaped lifecycle, and
the scim2-tester RFC checks that don't depend on the domain-claim rule."""

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from src.models.orm.identity_providers import OrganizationDomain
from src.models.orm.users import User, UserRole
from tests.e2e.conftest import E2E_API_URL

SCIM_JSON = "application/scim+json"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def _ok(response: httpx.Response, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@pytest.fixture
def scim_connection(e2e_client, platform_admin, db_session):
    """An org with a verified domain and a SCIM connection."""
    admin = platform_admin.headers
    tag = uuid.uuid4().hex[:8]
    organization = _ok(
        e2e_client.post("/api/organizations", headers=admin, json={"name": f"SCIM {tag}"}), 201
    )
    domain = f"scim-{tag}.example"
    claim = _ok(
        e2e_client.post(
            "/api/identity-providers/domains",
            headers=admin,
            json={"organization_id": organization["id"], "domain": domain},
        ),
        201,
    )
    assert claim["txt_record_value"].startswith("bifrost-verification=")
    created = _ok(
        e2e_client.post(
            "/api/identity-providers",
            headers=admin,
            json={"organization_id": organization["id"], "name": f"Entra {tag}", "vendor": "entra"},
        ),
        201,
    )
    yield {
        "organization": organization,
        "domain": domain,
        "claim_id": claim["id"],
        "connection": created["identity_provider"],
        "token": created["token"]["token"],
        "path": f"/api/scim/v2/{created['identity_provider']['id']}",
    }
    e2e_client.delete(
        f"/api/identity-providers/{created['identity_provider']['id']}", headers=admin
    )
    e2e_client.delete(f"/api/organizations/{organization['id']}", headers=admin)


async def _verify_domain_out_of_band(db_session, claim_id: str) -> None:
    # Real verification needs public DNS; mark it as the verify step would.
    await db_session.execute(
        update(OrganizationDomain)
        .where(OrganizationDomain.id == uuid.UUID(claim_id))
        .values(verified_at=datetime.now(timezone.utc))
    )
    await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_scim_user_and_group_lifecycle(e2e_client, platform_admin, db_session, scim_connection):
    admin = platform_admin.headers
    path, domain = scim_connection["path"], scim_connection["domain"]
    scim = {"Authorization": f"Bearer {scim_connection['token']}", "Content-Type": SCIM_JSON}

    refused = e2e_client.get(f"{path}/Users", headers={"Authorization": "Bearer bfidp_wrong"})
    assert refused.status_code == 401
    assert refused.headers["content-type"].startswith(SCIM_JSON)
    assert refused.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]

    email = f"pat@{domain}"
    entra_user = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User", ENTERPRISE],
        "userName": email,
        "externalId": "entra-object-1",
        "active": "True",
        "displayName": "Pat Example",
        "emails": [{"value": email, "type": "work", "primary": True}],
        ENTERPRISE: {"department": "Support"},
    }
    unclaimed = {**entra_user, "userName": "pat@unclaimed.example", "externalId": "x",
                 "emails": [{"value": "pat@unclaimed.example", "primary": True}]}
    await _verify_domain_out_of_band(db_session, scim_connection["claim_id"])
    assert e2e_client.post(f"{path}/Users", headers=scim, json=unclaimed).status_code == 400

    created = e2e_client.post(f"{path}/Users", headers=scim, json=entra_user)
    body = _ok(created, 201)
    assert created.headers["location"].endswith(f"/Users/{body['id']}")
    assert body["active"] is True and body[ENTERPRISE]["department"] == "Support"
    assert _ok(e2e_client.post(f"{path}/Users", headers=scim, json=entra_user), 409)["scimType"] == "uniqueness"

    found = _ok(e2e_client.get(f"{path}/Users", headers=scim, params={"filter": f'userName eq "{email.upper()}"'}))
    assert [r["id"] for r in found["Resources"]] == [body["id"]]

    users = _ok(e2e_client.get("/api/users", headers=admin, params={"organization_id": scim_connection["organization"]["id"]}))
    user = next(u for u in users if u["email"] == email)
    assert user["identity_source"]["name"] == scim_connection["connection"]["name"]

    group = _ok(e2e_client.post(f"{path}/Groups", headers=scim, json={
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": "Support Techs", "externalId": "entra-group-1",
        "members": [{"value": body["id"]}],
    }), 201)
    role = _ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"Support Tech {uuid.uuid4().hex[:6]}"}), 201)
    try:
        mapped = _ok(e2e_client.put(
            f"/api/identity-providers/{scim_connection['connection']['id']}/groups/{group['id']}/mapping",
            headers=admin, json={"role_ids": [role["id"]]},
        ))
        assert mapped["mapped"] and mapped["member_count"] == 1
        user_id = uuid.UUID(user["id"])
        db_session.expire_all()
        grant = await db_session.scalar(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == uuid.UUID(role["id"]))
        )
        assert grant is not None
        assert str(grant.identity_provider_id) == scim_connection["connection"]["id"]

        # Okta removes members with a value list rather than a path filter.
        removed = e2e_client.patch(f"{path}/Groups/{group['id']}", headers=scim, json={
            "schemas": [PATCH], "Operations": [{"op": "remove", "path": "members", "value": [{"value": body["id"]}]}],
        })
        assert removed.status_code == 204, removed.text
        db_session.expire_all()
        account = await db_session.get(User, user_id)
        assert account is not None and not account.is_active
        assert await db_session.scalar(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == uuid.UUID(role["id"]))
        ) is None

        rotated = _ok(e2e_client.post(
            f"/api/identity-providers/{scim_connection['connection']['id']}/token", headers=admin, json={"lifetime_days": 90},
        ))
        assert e2e_client.get(f"{path}/Users/{body['id']}", headers=scim).status_code == 200
        new_scim = {**scim, "Authorization": f"Bearer {rotated['token']}"}
        patched = _ok(e2e_client.patch(f"{path}/Users/{body['id']}", headers=new_scim, json={
            "schemas": [PATCH], "Operations": [{"op": "Replace", "path": "active", "value": "False"}],
        }))
        assert patched["active"] is False

        assert e2e_client.delete(f"{path}/Users/{body['id']}", headers=new_scim).status_code == 204
        db_session.expire_all()
        assert await db_session.get(User, user_id) is None
    finally:
        e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)


class _HttpxScimClient:
    """scim2-client engine over plain httpx (the bundled one needs httpx2)."""

    @staticmethod
    def build(base_url: str, token: str):
        from scim2_client import BaseSyncSCIMClient

        class Client(BaseSyncSCIMClient):
            def __init__(self) -> None:
                super().__init__()
                self.http = httpx.Client(
                    base_url=base_url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30.0,
                )

            def _stays_under_base_url(self, endpoint: str) -> bool:
                return endpoint.startswith(base_url) or not endpoint.startswith("http")

            def _parse_json(self, response):
                return response.json()

            def request(self, method: str, url: str, **kwargs):
                return self.http.request(method, url, **kwargs)

        return Client()


@pytest.mark.e2e
def test_scim2_tester_discovery_and_group_crud(scim_connection):
    from scim2_tester import Status, check_server

    client = _HttpxScimClient.build(f"{E2E_API_URL}{scim_connection['path']}", scim_connection["token"])
    client.discover()
    results = check_server(client, include_tags={"discovery", "crud"}, resource_types=["Group"]) + check_server(
        client, include_tags={"discovery"}
    )

    # Some Group checks first create member Users from generated data: a
    # non-email userName or a random email domain. Refusing those is the
    # domain-claim rule working, so only those two refusals are expected.
    provisioning_refusals = (
        "The user needs an email address or an email userName",
        "is not a verified domain of this organization",
    )
    failures = [
        f"{r.resource_type}: {r.title}: {r.reason}"
        for r in results
        if r.status in (Status.ERROR, Status.CRITICAL)
        and not any(refusal in (r.reason or "") for refusal in provisioning_refusals)
    ]
    assert not failures, "\n".join(failures)
    passed = {r.title for r in results if r.status is Status.SUCCESS}
    assert {"service_provider_config_endpoint", "object_creation", "object_deletion"} <= passed, passed
