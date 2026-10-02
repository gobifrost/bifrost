"""E2E: identity routes decided by the evaluator (R3a).

A Platform Admin gives a provider-org member the Platform Operator role at
managed organizations through the role-assignments endpoint. The operator
then works one customer organization: reads its users and the organization,
supports an ordinary user (MFA reset, deactivate), invites a user, assigns a
permissionless role; and is refused everything beyond that: lifecycle
fields, base-role changes, roles that carry permissions, privileged users,
the provider org, Global, and organization lifecycle. A regular user is
refused every identity route. A workflow's engine token keeps listing users
over the worker socket.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register
from tests.e2e.fixtures.setup import PROVIDER_ORG_ID, _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e

PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000007"
USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _ok(response, status: int = 200) -> Any:
    assert response.status_code == status, (
        f"{response.request.method} {response.request.url}: {response.status_code} {response.text}"
    )
    return response.json() if response.content else None


def _create_user(client, admin, *, org_id, tag: str, name: str) -> dict:
    return _ok(
        client.post(
            "/api/users",
            headers=admin.headers,
            json={"email": f"r3a-{name}-{tag}@example.com", "name": f"R3a {name}", "organization_id": org_id},
        ),
        201,
    )


def _create_role(client, admin, *, tag: str, name: str, permissions: list[str] = ()) -> dict:
    role = _ok(client.post("/api/roles", headers=admin.headers, json={"name": f"r3a-{name}-{tag}"}), 201)
    if permissions:
        _ok(
            client.put(
                f"/api/roles/{role['id']}/permissions",
                headers=admin.headers,
                json={"permissions": list(permissions)},
            )
        )
    return role


def _assign(client, headers, user_id: str, *, base: str = USER_ROLE_ID, additional=()):
    return client.put(
        f"/api/users/{user_id}/role-assignments",
        headers=headers,
        json={"base_role_id": base, "additional": list(additional)},
    )


@pytest.fixture(scope="module")
def world(e2e_client, platform_admin):
    """A customer org with an ordinary and a privileged user, two roles, and
    a provider-org operator. Everything is removed afterwards."""
    tag = _tag()
    org = _ok(e2e_client.post("/api/organizations", headers=platform_admin.headers, json={"name": f"r3a-org-{tag}"}), 201)
    ordinary = _create_user(e2e_client, platform_admin, org_id=org["id"], tag=tag, name="ordinary")
    privileged = _create_user(e2e_client, platform_admin, org_id=org["id"], tag=tag, name="privileged")
    plain_role = _create_role(e2e_client, platform_admin, tag=tag, name="plain")
    support_role = _create_role(e2e_client, platform_admin, tag=tag, name="support", permissions=["users.readwrite"])
    _ok(_assign(e2e_client, platform_admin.headers, privileged["id"], additional=[{"role_id": support_role["id"]}]))

    operator = E2EUser(
        email=f"r3a-operator-{tag}@example.com",
        password="R3aOperator123!",
        name="R3a Operator",
        organization_id=PROVIDER_ORG_ID,
    )
    created = _ok(
        e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={"email": operator.email, "name": operator.name, "organization_id": str(PROVIDER_ORG_ID)},
        ),
        201,
    )
    operator = _register_and_authenticate_user(operator)
    assigned = _ok(
        _assign(
            e2e_client,
            platform_admin.headers,
            created["id"],
            additional=[{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
        )
    )
    assert [a["role_id"] for a in assigned["additional"]] == [PLATFORM_OPERATOR_ROLE_ID]
    assert assigned["is_protected"] is True

    yield {
        "tag": tag,
        "org": org,
        "ordinary": ordinary,
        "privileged": privileged,
        "operator": operator,
        "operator_id": created["id"],
        "plain_role": plain_role,
        "support_role": support_role,
    }

    for user_id in (ordinary["id"], privileged["id"], created["id"]):
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)
    for role in (plain_role, support_role):
        e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)
    e2e_client.delete(f"/api/organizations/{org['id']}", headers=platform_admin.headers)


class TestPlatformOperator:
    def test_reads_customer_users_only(self, e2e_client, world, platform_admin) -> None:
        response = e2e_client.get("/api/users", headers=world["operator"].headers, params={"include_inactive": True})
        users = _ok(response)
        orgs = {u["organization_id"] for u in users}
        assert world["org"]["id"] in orgs
        assert str(PROVIDER_ORG_ID) not in orgs
        assert None not in orgs
        assert int(response.headers["X-Total-Count"]) == len(users)
        by_id = {u["id"]: u for u in users}
        assert by_id[world["privileged"]["id"]]["is_protected"] is True
        assert by_id[world["ordinary"]["id"]]["is_protected"] is False

        for scope in (str(PROVIDER_ORG_ID), "global"):
            assert e2e_client.get("/api/users", headers=world["operator"].headers, params={"scope": scope}).status_code == 403
        assert e2e_client.get(f"/api/users/{platform_admin.user_id}", headers=world["operator"].headers).status_code == 403

    def test_supports_an_ordinary_customer_user(self, e2e_client, world) -> None:
        headers = world["operator"].headers
        user_id = world["ordinary"]["id"]
        _ok(e2e_client.patch(f"/api/users/{user_id}", headers=headers, json={"mfa_enabled": False}))
        assert _ok(e2e_client.patch(f"/api/users/{user_id}", headers=headers, json={"is_active": False}))["is_active"] is False
        _ok(e2e_client.patch(f"/api/users/{user_id}", headers=headers, json={"is_active": True}))
        _ok(e2e_client.post(f"/api/users/{user_id}/invite/regenerate", headers=headers))
        assert e2e_client.patch(
            f"/api/users/{user_id}", headers=headers, json={"email": f"moved-{world['tag']}@example.com"}
        ).status_code == 403

    def test_invites_an_ordinary_user_only(self, e2e_client, world, platform_admin) -> None:
        headers = world["operator"].headers
        invited = _ok(
            e2e_client.post(
                "/api/users",
                headers=headers,
                json={"email": f"r3a-invited-{world['tag']}@example.com", "organization_id": world["org"]["id"]},
            ),
            201,
        )
        try:
            assert invited["is_superuser"] is False
            refused = [
                {"email": f"r3a-admin-{world['tag']}@example.com", "is_superuser": True, "organization_id": str(PROVIDER_ORG_ID)},
                {"email": f"r3a-global-{world['tag']}@example.com"},
            ]
            for body in refused:
                assert e2e_client.post("/api/users", headers=headers, json=body).status_code == 403
        finally:
            e2e_client.delete(f"/api/users/{invited['id']}", headers=platform_admin.headers)

    def test_privileged_users_are_protected(self, e2e_client, world, platform_admin) -> None:
        headers = world["operator"].headers
        response = e2e_client.patch(
            f"/api/users/{world['privileged']['id']}", headers=headers, json={"mfa_enabled": False}
        )
        assert response.status_code == 403, response.text
        assert e2e_client.post(
            "/auth/admin/revoke-user", headers=headers, json={"user_id": str(platform_admin.user_id)}
        ).status_code == 403

    def test_assigns_only_permissionless_roles(self, e2e_client, world) -> None:
        headers = world["operator"].headers
        user_id = world["ordinary"]["id"]
        view = _ok(e2e_client.get(f"/api/users/{user_id}/role-assignments", headers=headers))
        assignable = {r["id"]: r for r in view["assignable_roles"]}
        assert world["plain_role"]["id"] in assignable
        assert world["support_role"]["id"] not in assignable
        assert PLATFORM_OPERATOR_ROLE_ID not in assignable

        assigned = _ok(_assign(e2e_client, headers, user_id, additional=[{"role_id": world["plain_role"]["id"]}]))
        assert [a["boundaries"] for a in assigned["additional"]] == [
            [{"kind": "organization", "organization_id": world["org"]["id"], "organization_name": world["org"]["name"]}]
        ]
        assert _assign(
            e2e_client, headers, user_id, additional=[{"role_id": world["support_role"]["id"]}]
        ).status_code == 403
        assert _assign(e2e_client, headers, user_id, base=world["plain_role"]["id"]).status_code == 403

    def test_reads_customer_orgs_but_never_manages_them(self, e2e_client, world) -> None:
        headers = world["operator"].headers
        org_ids = {o["id"] for o in _ok(e2e_client.get("/api/organizations", headers=headers))}
        assert world["org"]["id"] in org_ids and str(PROVIDER_ORG_ID) not in org_ids
        _ok(e2e_client.get(f"/api/organizations/{world['org']['id']}", headers=headers))
        assert e2e_client.get(f"/api/organizations/{PROVIDER_ORG_ID}", headers=headers).status_code == 403
        assert e2e_client.post("/api/organizations", headers=headers, json={"name": f"no-{world['tag']}"}).status_code == 403
        assert e2e_client.patch(
            f"/api/organizations/{world['org']['id']}", headers=headers, json={"name": "renamed"}
        ).status_code == 403
        assert e2e_client.get("/api/roles", headers=headers).status_code == 403

    def test_own_authorization_summary(self, e2e_client, world, platform_admin) -> None:
        summary = _ok(e2e_client.get("/auth/authorization", headers=world["operator"].headers))
        assert summary["is_platform_admin"] is False
        assert {"permission": "users.readwrite", "boundary": {"kind": "managed_organizations", "organization_id": None}} in summary["grants"]
        admin = _ok(e2e_client.get("/auth/authorization", headers=platform_admin.headers))
        assert admin["is_platform_admin"] is True and admin["grants"] == []


def test_operator_is_only_for_provider_org_people(e2e_client, world, platform_admin) -> None:
    response = _assign(
        e2e_client,
        platform_admin.headers,
        world["ordinary"]["id"],
        additional=[{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == (
        "Platform Operator can only be given to people in the provider organization"
    )


def test_regular_user_is_refused_every_identity_route(e2e_client, org1_user, world) -> None:
    headers = org1_user.headers
    user_id = world["ordinary"]["id"]
    requests = [
        ("GET", "/api/users", None),
        ("GET", f"/api/users/{user_id}", None),
        ("POST", "/api/users", {"email": f"nope-{world['tag']}@example.com", "organization_id": str(org1_user.organization_id)}),
        ("PATCH", f"/api/users/{user_id}", {"name": "x"}),
        ("GET", f"/api/users/{user_id}/role-assignments", None),
        ("GET", "/api/organizations", None),
        ("GET", f"/api/organizations/{org1_user.organization_id}", None),
        ("GET", "/api/roles", None),
        ("GET", f"/api/roles/{world['plain_role']['id']}/permissions", None),
        ("GET", f"/api/roles/{world['plain_role']['id']}/users", None),
    ]
    for method, path, body in requests:
        response = e2e_client.request(method, path, headers=headers, json=body)
        assert response.status_code == 403, f"{method} {path}: {response.status_code} {response.text}"
    assert _ok(e2e_client.get("/auth/authorization", headers=headers))["is_platform_admin"] is False


def test_engine_token_lists_users_over_the_worker_socket(e2e_client, platform_admin, org1, org1_user) -> None:
    name = f"e2e_r3a_users_list_{_tag()}"
    path = f"{name}.py"
    content = f'''"""R3a engine users.list."""
from bifrost import users, workflow
from bifrost.client import get_engine_socket_path

@workflow(name="{name}", description="R3a engine users.list")
async def {name}():
    listed = await users.list()
    return {{"used_socket": get_engine_socket_path() is not None, "count": len(listed)}}
'''
    registered = write_and_register(
        e2e_client, platform_admin.headers, path, content, name, organization_id=org1["id"]
    )
    try:
        _ok(
            e2e_client.patch(
                f"/api/workflows/{registered['id']}",
                headers=platform_admin.headers,
                json={"organization_id": org1["id"], "access_level": "authenticated"},
            )
        )
        result = execute_workflow_sync(e2e_client, org1_user.headers, registered["id"], max_wait=120.0)
        assert result["status"] == "Success", result
        assert result["result"]["used_socket"] is True
        assert result["result"]["count"] > 0
    finally:
        e2e_client.delete(f"/api/files/editor?path={path}", headers=platform_admin.headers)
