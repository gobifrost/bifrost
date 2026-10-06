"""``GET /api/users/{id}/access``: gated like reading the user's role
assignments. A Platform Admin reads anyone's map, a Platform Operator reads
people at the customer organizations their role reaches (not the provider
organization), and a plain user reads nobody's."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.e2e.fixtures.setup import PROVIDER_ORG_ID, _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e

PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000007"
USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"


def _ok(response, status: int = 200) -> Any:
    assert response.status_code == status, f"{response.request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else None


@pytest.fixture(scope="module")
def world(e2e_client, platform_admin):
    tag = uuid.uuid4().hex[:8]
    customer = _ok(
        e2e_client.post("/api/organizations", headers=platform_admin.headers, json={"name": f"access-org-{tag}"}), 201
    )
    created_ids = []

    def create_user(email: str, organization_id: str) -> dict:
        user = _ok(
            e2e_client.post(
                "/api/users",
                headers=platform_admin.headers,
                json={"email": email, "name": "Access Person", "organization_id": organization_id},
            ),
            201,
        )
        created_ids.append(user["id"])
        return user

    customer_user = create_user(f"access-customer-{tag}@example.com", customer["id"])
    provider_user = create_user(f"access-provider-{tag}@example.com", str(PROVIDER_ORG_ID))
    operator = E2EUser(
        email=f"access-operator-{tag}@example.com",
        password="AccessOperator123!",
        name="Access Operator",
        organization_id=PROVIDER_ORG_ID,
    )
    operator_row = create_user(operator.email, str(PROVIDER_ORG_ID))
    operator = _register_and_authenticate_user(operator)
    _ok(
        e2e_client.put(
            f"/api/users/{operator_row['id']}/role-assignments",
            headers=platform_admin.headers,
            json={
                "base_role_id": USER_ROLE_ID,
                "additional": [{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
            },
        )
    )
    yield {
        "customer": customer,
        "customer_user": customer_user,
        "provider_user": provider_user,
        "operator": operator,
        "operator_row": operator_row,
    }
    for user_id in created_ids:
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)
    e2e_client.delete(f"/api/organizations/{customer['id']}", headers=platform_admin.headers)


def test_platform_admin_reads_a_customer_users_map(e2e_client, platform_admin, world) -> None:
    body = _ok(e2e_client.get(f"/api/users/{world['customer_user']['id']}/access", headers=platform_admin.headers))

    customer = world["customer"]
    assert body["user_id"] == world["customer_user"]["id"]
    assert body["home_organization"] == {"id": customer["id"], "name": customer["name"]}
    assert body["is_platform_admin"] is False
    assert [p["label"] for p in body["reach"]] == [f"{customer['name']} (home)", "Global"]
    [row] = body["rows"]
    assert row["place"]["kind"] == "home"
    grants = {g["permission"]: g for g in row["grants"]}
    assert grants, "the User base role holds something at home"
    for grant in grants.values():
        assert [(s["role_name"], s["via"]) for s in grant["sources"]] == [("User", "base")]


def test_platform_admins_map_is_the_wildcard_for_all_organizations(e2e_client, platform_admin, world) -> None:
    body = _ok(e2e_client.get(f"/api/users/{platform_admin.user_id}/access", headers=platform_admin.headers))

    assert body["is_platform_admin"] is True
    assert body["is_protected"] is True
    platform_rows = [row for row in body["rows"] if row["place"]["kind"] == "platform"]
    [row] = platform_rows
    assert row["place"]["label"] == "All organizations"
    assert [g["permission"] for g in row["grants"]] == ["*"]


def test_operator_reads_a_customer_users_map(e2e_client, world) -> None:
    _ok(e2e_client.get(f"/api/users/{world['customer_user']['id']}/access", headers=world["operator"].headers))


def test_operator_cannot_read_a_provider_users_map(e2e_client, world) -> None:
    response = e2e_client.get(f"/api/users/{world['provider_user']['id']}/access", headers=world["operator"].headers)
    assert response.status_code == 403, response.text


def test_operators_own_map_shows_where_the_role_applies(e2e_client, platform_admin, world) -> None:
    body = _ok(e2e_client.get(f"/api/users/{world['operator_row']['id']}/access", headers=platform_admin.headers))

    places = [row["place"]["label"] for row in body["rows"]]
    assert "All customer organizations" in places
    managed = next(row for row in body["rows"] if row["place"]["kind"] == "managed_organizations")
    assert all(
        [(s["role_name"], s["via"]) for s in g["sources"]] == [("Platform Operator", "additional")]
        for g in managed["grants"]
    )


def test_plain_user_is_refused(e2e_client, org1_user, world) -> None:
    response = e2e_client.get(f"/api/users/{world['customer_user']['id']}/access", headers=org1_user.headers)
    assert response.status_code == 403, response.text


def test_unknown_user_is_not_found_for_an_admin(e2e_client, platform_admin) -> None:
    response = e2e_client.get(f"/api/users/{uuid.uuid4()}/access", headers=platform_admin.headers)
    assert response.status_code == 404, response.text
