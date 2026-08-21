"""
E2E tests for the bulk users endpoint (PATCH /api/users/bulk).

Covers the three operations (move_org, replace_roles, set_active) plus
the guard cases that must end up in `failed` rather than mutating state.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.constants import SYSTEM_USER_ID


def _create_user(e2e_client, platform_admin, org_id: str, email_prefix: str) -> str:
    """Create a user and return its id."""
    resp = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": f"{email_prefix}-{uuid.uuid4().hex[:8]}@bulk.gobifrost.dev",
            "name": f"Bulk Test {email_prefix}",
            "organization_id": org_id,
            "is_superuser": False,
        },
    )
    assert resp.status_code == 201, f"Create user failed: {resp.text}"
    return resp.json()["id"]


def _create_role(e2e_client, platform_admin, name_suffix: str) -> str:
    """Create a role and return its id."""
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={
            "name": f"Bulk Test Role {name_suffix} {uuid.uuid4().hex[:6]}",
            "description": "bulk test",
        },
    )
    assert resp.status_code == 201, f"Create role failed: {resp.text}"
    return resp.json()["id"]


@pytest.mark.e2e
class TestBulkMoveOrg:
    """PATCH /api/users/bulk operation=move_org."""

    def test_move_org_happy_path(self, e2e_client, platform_admin, org1, org2):
        """Moving users between orgs updates organization_id on every target."""
        user_a = _create_user(e2e_client, platform_admin, org1["id"], "moveA")
        user_b = _create_user(e2e_client, platform_admin, org1["id"], "moveB")

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_a, user_b],
                "operation": "move_org",
                "organization_id": org2["id"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["succeeded"]) == {user_a, user_b}
        assert body["failed"] == []

        # Verify both users are now in org2
        for uid in (user_a, user_b):
            get_resp = e2e_client.get(f"/api/users/{uid}", headers=platform_admin.headers)
            assert get_resp.status_code == 200
            assert get_resp.json()["organization_id"] == org2["id"]

    def test_move_org_refuses_platform_admin_to_non_provider(
        self, e2e_client, platform_admin, org1
    ):
        """Platform admins cannot be silently demoted by being moved to a non-provider org."""
        # Create a platform admin (is_superuser=True)
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": f"superadmin-{uuid.uuid4().hex[:8]}@bulk.gobifrost.dev",
                "name": "Bulk Test Super",
                "organization_id": None,
                "is_superuser": True,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        super_id = create_resp.json()["id"]

        # Also create a normal user so we see partial-success behavior
        normal_id = _create_user(e2e_client, platform_admin, org1["id"], "moveNormal")

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [super_id, normal_id],
                "operation": "move_org",
                "organization_id": org1["id"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert normal_id in body["succeeded"]
        failed_ids = [f["user_id"] for f in body["failed"]]
        assert super_id in failed_ids
        reason = next(f["reason"] for f in body["failed"] if f["user_id"] == super_id)
        assert "platform admin" in reason.lower()


@pytest.mark.e2e
class TestBulkReplaceRoles:
    """PATCH /api/users/bulk operation=replace_roles."""

    def test_replace_roles_overwrites_previous_set(self, e2e_client, platform_admin, org1):
        """replace_roles is a full overwrite — old roles disappear, new ones appear."""
        user_id = _create_user(e2e_client, platform_admin, org1["id"], "rolesUser")
        old_role = _create_role(e2e_client, platform_admin, "Old")
        new_role_a = _create_role(e2e_client, platform_admin, "NewA")
        new_role_b = _create_role(e2e_client, platform_admin, "NewB")

        # Pre-assign old role
        assign_resp = e2e_client.post(
            f"/api/roles/{old_role}/users",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_id],
                "boundaries": [
                    {
                        "boundary_kind": "organization",
                        "organization_id": org1["id"],
                    }
                ],
            },
        )
        assert assign_resp.status_code == 204, assign_resp.text

        # Sanity: old role contains the user
        before = e2e_client.get(
            f"/api/roles/{old_role}/users", headers=platform_admin.headers
        ).json()["user_ids"]
        assert user_id in before

        # Replace with new role set
        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_id],
                "operation": "replace_roles",
                "role_assignments": [
                    {
                        "role_id": role_id,
                        "boundaries": [
                            {
                                "boundary_kind": "organization",
                                "organization_id": org1["id"],
                            }
                        ],
                    }
                    for role_id in (new_role_a, new_role_b)
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["succeeded"] == [user_id]
        assert resp.json()["failed"] == []

        # Old role: user gone
        after_old = e2e_client.get(
            f"/api/roles/{old_role}/users", headers=platform_admin.headers
        ).json()["user_ids"]
        assert user_id not in after_old

        # Each new role: user present
        for rid in (new_role_a, new_role_b):
            after_new = e2e_client.get(
                f"/api/roles/{rid}/users", headers=platform_admin.headers
            ).json()["user_ids"]
            assert user_id in after_new, f"User missing from new role {rid}"

    def test_replace_roles_empty_list_clears_all(self, e2e_client, platform_admin, org1):
        """Passing role_ids=[] clears every role assignment for the target users."""
        user_id = _create_user(e2e_client, platform_admin, org1["id"], "rolesClear")
        role_id = _create_role(e2e_client, platform_admin, "ToClear")

        assign_resp = e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_id],
                "boundaries": [
                    {
                        "boundary_kind": "organization",
                        "organization_id": org1["id"],
                    }
                ],
            },
        )
        assert assign_resp.status_code == 204

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_id],
                "operation": "replace_roles",
                "role_assignments": [],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["succeeded"] == [user_id]

        after = e2e_client.get(
            f"/api/roles/{role_id}/users", headers=platform_admin.headers
        ).json()["user_ids"]
        assert user_id not in after

    def test_replace_roles_self_in_selection_fails(self, e2e_client, platform_admin):
        """The acting user appears in `failed` with a clear reason instead of locking themselves out."""
        role_id = _create_role(e2e_client, platform_admin, "SelfRole")

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [str(platform_admin.user_id)],
                "operation": "replace_roles",
                "role_assignments": [
                    {
                        "role_id": role_id,
                        "boundaries": [{"boundary_kind": "platform"}],
                    }
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == []
        failed_ids = [f["user_id"] for f in body["failed"]]
        assert str(platform_admin.user_id) in failed_ids


@pytest.mark.e2e
class TestBulkSetActive:
    """PATCH /api/users/bulk operation=set_active."""

    def test_set_active_false_disables_user(self, e2e_client, platform_admin, org1):
        """set_active=false should disable the user (hidden from default listing)."""
        user_id = _create_user(e2e_client, platform_admin, org1["id"], "disableMe")

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_id],
                "operation": "set_active",
                "is_active": False,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["succeeded"] == [user_id]

        # Default listing hides inactive users
        list_resp = e2e_client.get("/api/users", headers=platform_admin.headers)
        assert list_resp.status_code == 200
        active_ids = [u["id"] for u in list_resp.json()]
        assert user_id not in active_ids

        # include_inactive=true shows them
        full_resp = e2e_client.get(
            "/api/users",
            headers=platform_admin.headers,
            params={"include_inactive": "true"},
        )
        match = next((u for u in full_resp.json() if u["id"] == user_id), None)
        assert match is not None
        assert match["is_active"] is False

    def test_set_active_self_fails(self, e2e_client, platform_admin):
        """Self in selection → recorded in failed, never disabled."""
        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [str(platform_admin.user_id)],
                "operation": "set_active",
                "is_active": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == []
        assert [f["user_id"] for f in body["failed"]] == [str(platform_admin.user_id)]


@pytest.mark.e2e
class TestBulkSystemUserGuard:
    """System user must never be modifiable, regardless of operation."""

    def test_system_user_in_selection_fails(self, e2e_client, platform_admin, org1):
        """System user appears in `failed` with reason, others still succeed."""
        normal_id = _create_user(e2e_client, platform_admin, org1["id"], "withSystem")

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [SYSTEM_USER_ID, normal_id],
                "operation": "set_active",
                "is_active": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert normal_id in body["succeeded"]
        failed_ids = [f["user_id"] for f in body["failed"]]
        assert SYSTEM_USER_ID in failed_ids
        reason = next(f["reason"] for f in body["failed"] if f["user_id"] == SYSTEM_USER_ID)
        assert "system user" in reason.lower()


@pytest.mark.e2e
class TestBulkValidation:
    """Pydantic-level validation rejection cases."""

    def test_empty_user_ids_rejected(self, e2e_client, platform_admin):
        """user_ids must be non-empty (min_length=1)."""
        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [],
                "operation": "set_active",
                "is_active": True,
            },
        )
        assert resp.status_code == 422, resp.text

    def test_unknown_operation_rejected(self, e2e_client, platform_admin, org1):
        """Unknown operation strings are rejected by the model validator."""
        user_id = _create_user(e2e_client, platform_admin, org1["id"], "unknownOp")

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_id],
                "operation": "do_a_barrel_roll",
            },
        )
        assert resp.status_code == 422, resp.text

    def test_replace_roles_missing_role_ids_rejected(
        self, e2e_client, platform_admin, org1
    ):
        """replace_roles without role_ids is a validation error."""
        user_id = _create_user(e2e_client, platform_admin, org1["id"], "missingRoles")

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_id],
                "operation": "replace_roles",
            },
        )
        assert resp.status_code == 422, resp.text

    def test_set_active_missing_flag_rejected(self, e2e_client, platform_admin, org1):
        """set_active without is_active is a validation error."""
        user_id = _create_user(e2e_client, platform_admin, org1["id"], "missingFlag")

        resp = e2e_client.patch(
            "/api/users/bulk",
            headers=platform_admin.headers,
            json={
                "user_ids": [user_id],
                "operation": "set_active",
            },
        )
        assert resp.status_code == 422, resp.text
