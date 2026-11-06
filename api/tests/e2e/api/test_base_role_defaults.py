"""API contract for configured base roles used during account provisioning."""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared.builtin_roles import USER_ROLE_ID


def _create_role(e2e_client, headers, label: str) -> dict:
    response = e2e_client.post(
        "/api/roles",
        headers=headers,
        json={"name": f"Default Base {label} {uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.e2e
def test_defaults_precedence_provisioning_reset_permissions_and_role_references(
    e2e_client, platform_admin, org1, org1_user
):
    headers = platform_admin.headers
    initial_global = e2e_client.get("/api/base-role-defaults", headers=headers)
    assert initial_global.status_code == 200, initial_global.text
    initial_org = e2e_client.get(
        "/api/base-role-defaults",
        headers=headers,
        params={"organization_id": org1["id"]},
    )
    assert initial_org.status_code == 200, initial_org.text
    global_role = _create_role(e2e_client, headers, "Global")
    organization_role = _create_role(e2e_client, headers, "Organization")
    created_user_id: str | None = None
    explicit_user_id: str | None = None
    custom_identity_id: str | None = None
    try:
        forbidden = e2e_client.get(
            "/api/base-role-defaults",
            headers=org1_user.headers,
            params={"organization_id": org1["id"]},
        )
        assert forbidden.status_code == 403, forbidden.text

        global_set = e2e_client.put(
            "/api/base-role-defaults",
            headers=headers,
            json={"default_base_role_id": global_role["id"]},
        )
        assert global_set.status_code == 200, global_set.text
        assert global_set.json() == {
            "default_base_role_id": global_role["id"],
            "effective_base_role_id": global_role["id"],
            "effective_base_role_name": global_role["name"],
            "source": "global",
        }

        inherited = e2e_client.get(
            "/api/base-role-defaults",
            headers=headers,
            params={"organization_id": org1["id"]},
        )
        assert inherited.status_code == 200, inherited.text
        assert inherited.json()["default_base_role_id"] is None
        assert inherited.json()["effective_base_role_id"] == global_role["id"]
        assert inherited.json()["source"] == "global"

        protected_global = e2e_client.delete(
            f"/api/roles/{global_role['id']}", headers=headers
        )
        assert protected_global.status_code == 409, protected_global.text

        org_set = e2e_client.put(
            "/api/base-role-defaults",
            headers=headers,
            params={"organization_id": org1["id"]},
            json={"default_base_role_id": organization_role["id"]},
        )
        assert org_set.status_code == 200, org_set.text
        assert org_set.json()["effective_base_role_id"] == organization_role["id"]
        assert org_set.json()["source"] == "organization"

        protected_org = e2e_client.delete(
            f"/api/roles/{organization_role['id']}", headers=headers
        )
        assert protected_org.status_code == 409, protected_org.text

        created = e2e_client.post(
            "/api/users",
            headers=headers,
            json={
                "email": f"default-base-{uuid4().hex[:8]}@example.com",
                "name": "Default Base Provisioning",
                "organization_id": org1["id"],
            },
        )
        assert created.status_code == 201, created.text
        created_user_id = created.json()["id"]
        assignments = e2e_client.get(
            f"/api/users/{created_user_id}/role-assignments", headers=headers
        )
        assert assignments.status_code == 200, assignments.text
        assert assignments.json()["base_role"]["id"] == organization_role["id"]

        explicit_user = e2e_client.post(
            "/api/users", headers=headers,
            json={"email": f"explicit-base-{uuid4().hex[:8]}@example.com", "name": "Explicit Base", "organization_id": org1["id"], "base_role_id": global_role["id"]},
        )
        assert explicit_user.status_code == 201, explicit_user.text
        explicit_user_id = explicit_user.json()["id"]
        explicit_assignments = e2e_client.get(f"/api/users/{explicit_user_id}/role-assignments", headers=headers)
        assert explicit_assignments.status_code == 200, explicit_assignments.text
        assert explicit_assignments.json()["base_role"]["id"] == global_role["id"]

        custom_identity = e2e_client.post(
            "/api/identities", headers=headers,
            json={"name": f"Default Identity Test {uuid4().hex[:8]}", "organization_id": org1["id"]},
        )
        assert custom_identity.status_code == 201, custom_identity.text
        custom_identity_id = custom_identity.json()["id"]
        assert custom_identity.json()["base_role"]["id"] == organization_role["id"]

        inherited_again = e2e_client.put(
            "/api/base-role-defaults",
            headers=headers,
            params={"organization_id": org1["id"]},
            json={"default_base_role_id": None},
        )
        assert inherited_again.status_code == 200, inherited_again.text
        assert inherited_again.json()["default_base_role_id"] is None
        assert inherited_again.json()["effective_base_role_id"] == global_role["id"]

        reset = e2e_client.put(
            "/api/base-role-defaults",
            headers=headers,
            json={"default_base_role_id": None},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["default_base_role_id"] == str(USER_ROLE_ID)
        assert reset.json()["effective_base_role_id"] == str(USER_ROLE_ID)
        assert reset.json()["source"] == "global"
    finally:
        e2e_client.put(
            "/api/base-role-defaults",
            headers=headers,
            params={"organization_id": org1["id"]},
            json={"default_base_role_id": initial_org.json()["default_base_role_id"]},
        )
        e2e_client.put(
            "/api/base-role-defaults",
            headers=headers,
            json={"default_base_role_id": initial_global.json()["default_base_role_id"]},
        )
        for user_id in (created_user_id, explicit_user_id):
            if user_id is not None:
                e2e_client.delete(f"/api/users/{user_id}", headers=headers)
        if custom_identity_id is not None:
            e2e_client.delete(f"/api/identities/{custom_identity_id}", headers=headers)
        e2e_client.delete(f"/api/roles/{organization_role['id']}", headers=headers)
        e2e_client.delete(f"/api/roles/{global_role['id']}", headers=headers)
