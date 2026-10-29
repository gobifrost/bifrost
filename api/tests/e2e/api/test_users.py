"""
E2E tests for user management.

Tests user creation, listing, profile operations,
disable/enable, permanent delete, and system user protection.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select, update

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.core.constants import SYSTEM_USER_ID


@pytest.mark.e2e
class TestUserCreation:
    """Test user creation flows."""

    def test_org1_user_created(self, org1_user, org1):
        """Org1 user should be created via fixture."""
        assert org1_user.email == "alice@gobifrost.dev"
        assert org1_user.organization_id == org1["id"] or str(org1_user.organization_id) == org1["id"]
        assert org1_user.is_superuser is False
        assert org1_user.access_token is not None

    def test_org2_user_created(self, org2_user, org2):
        """Org2 user should be created via fixture."""
        assert org2_user.email == "bob@org2.gobifrost.com"
        assert org2_user.organization_id == org2["id"] or str(org2_user.organization_id) == org2["id"]
        assert org2_user.is_superuser is False
        assert org2_user.access_token is not None


@pytest.mark.e2e
class TestUserListing:
    """Test user listing."""

    def test_platform_admin_can_list_users(self, e2e_client, platform_admin, org1_user, org2_user):
        """Platform admin can list all users."""
        response = e2e_client.get(
            "/api/users",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List users failed: {response.text}"
        users = response.json()
        # Should have at least 3 users: admin + 2 org users
        assert len(users) >= 3

        emails = [u["email"] for u in users]
        assert platform_admin.email in emails
        assert org1_user.email in emails
        assert org2_user.email in emails

    def test_user_listing_supports_server_pagination_search_and_sort(
        self, e2e_client, platform_admin, org1
    ):
        marker = "User page boundary"
        created_ids = []
        try:
            for name, email in [
                (f"{marker} Alpha", "user-page-alpha@gobifrost.dev"),
                (f"{marker} Bravo", "user-page-bravo@gobifrost.dev"),
            ]:
                create = e2e_client.post(
                    "/api/users",
                    headers=platform_admin.headers,
                    json={
                        "name": name,
                        "email": email,
                        "organization_id": org1["id"],
                        "is_superuser": False,
                        "invite": False,
                    },
                )
                assert create.status_code == 201, create.text
                created_ids.append(create.json()["id"])

            first = e2e_client.get(
                "/api/users",
                headers=platform_admin.headers,
                params={
                    "search": marker,
                    "sort_by": "name",
                    "sort_direction": "asc",
                    "limit": 1,
                    "offset": 0,
                },
            )
            assert first.status_code == 200, first.text
            assert first.headers["X-Total-Count"] == "2"
            assert [user["name"] for user in first.json()] == [f"{marker} Alpha"]

            second = e2e_client.get(
                "/api/users",
                headers=platform_admin.headers,
                params={
                    "search": marker,
                    "sort_by": "name",
                    "sort_direction": "asc",
                    "limit": 1,
                    "offset": 1,
                },
            )
            assert [user["name"] for user in second.json()] == [f"{marker} Bravo"]
        finally:
            for user_id in created_ids:
                e2e_client.delete(
                    f"/api/users/{user_id}", headers=platform_admin.headers
                )

    def test_system_user_hidden_from_listing(self, e2e_client, platform_admin):
        """System user should never appear in user listings."""
        response = e2e_client.get(
            "/api/users",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        users = response.json()

        user_ids = [u["id"] for u in users]
        assert SYSTEM_USER_ID not in user_ids

        # Also check is_system flag — all returned users should have is_system=false
        for u in users:
            assert u["is_system"] is False, f"System user leaked in listing: {u['email']}"

    def test_inactive_users_hidden_by_default(self, e2e_client, platform_admin, org1):
        """Inactive users should be hidden when include_inactive is not set."""
        # Create a user to disable
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "inactive-test@gobifrost.dev",
                "name": "Inactive Test",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        # Disable the user
        disable_resp = e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        assert disable_resp.status_code == 200

        # Default listing should NOT include inactive user
        list_resp = e2e_client.get(
            "/api/users",
            headers=platform_admin.headers,
        )
        assert list_resp.status_code == 200
        user_ids = [u["id"] for u in list_resp.json()]
        assert user_id not in user_ids

        # With include_inactive=true, it SHOULD appear
        list_resp = e2e_client.get(
            "/api/users",
            headers=platform_admin.headers,
            params={"include_inactive": "true"},
        )
        assert list_resp.status_code == 200
        user_ids = [u["id"] for u in list_resp.json()]
        assert user_id in user_ids

        # Cleanup: permanently delete (already inactive)
        del_resp = e2e_client.delete(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
        )
        assert del_resp.status_code == 204


@pytest.mark.e2e
class TestUserProfile:
    """Test user profile operations."""

    def test_org_user_can_see_own_profile(self, e2e_client, org1_user):
        """Org user can access their own profile."""
        response = e2e_client.get("/auth/me", headers=org1_user.headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == org1_user.email
        assert data["is_superuser"] is False

    def test_org_user_can_view_mfa_status(self, e2e_client, org1_user):
        """Org user can check their MFA status."""
        response = e2e_client.get(
            "/auth/mfa/status",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["mfa_enabled"] is True


@pytest.mark.e2e
class TestSystemUserProtection:
    """Test that system user cannot be modified or deleted."""

    def test_cannot_update_system_user(self, e2e_client, platform_admin):
        """System user cannot be modified via PATCH."""
        response = e2e_client.patch(
            f"/api/users/{SYSTEM_USER_ID}",
            headers=platform_admin.headers,
            json={"name": "Hacked System User"},
        )
        assert response.status_code == 403
        assert "system user" in response.json()["detail"].lower()

    def test_cannot_delete_system_user(self, e2e_client, platform_admin):
        """System user cannot be deleted."""
        response = e2e_client.delete(
            f"/api/users/{SYSTEM_USER_ID}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 403
        assert "system user" in response.json()["detail"].lower()


@pytest.mark.e2e
class TestSelfProtection:
    """Test that admins cannot demote or delete themselves."""

    def test_cannot_delete_self_when_another_platform_admin_exists(
        self, e2e_client, platform_admin, second_platform_admin
    ):
        """Self-deletion remains a 400 while another active admin survives."""
        assert second_platform_admin.is_superuser is True
        response = e2e_client.delete(
            f"/api/users/{platform_admin.user_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 400
        assert "yourself" in response.json()["detail"].lower()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_last_active_platform_admin_rest_mutations_are_refused(
    e2e_client, platform_admin, db_session
):
    """REST mutation paths retain one active human Platform Admin, including self-delete."""
    from src.models import User as UserORM
    from src.models import UserRole

    rows = (
        await db_session.execute(
            select(UserORM.id, UserORM.is_active)
            .join(UserRole, UserRole.user_id == UserORM.id)
            .where(
                UserRole.role_id == PLATFORM_ADMIN_ROLE_ID,
                UserORM.id != platform_admin.user_id,
                UserORM.is_system.is_(False),
                UserORM.identity_kind.is_(None),
            )
        )
    ).all()
    try:
        if rows:
            await db_session.execute(
                update(UserORM)
                .where(UserORM.id.in_([row.id for row in rows]))
                .values(is_active=False)
            )
            await db_session.commit()

        responses = [
            e2e_client.patch(
                "/api/users/bulk",
                headers=platform_admin.headers,
                json={"user_ids": [str(platform_admin.user_id)], "operation": "set_active", "is_active": False},
            ),
            e2e_client.patch(
                f"/api/users/{platform_admin.user_id}",
                headers=platform_admin.headers,
                json={"is_superuser": False},
            ),
            e2e_client.patch(
                f"/api/users/{platform_admin.user_id}",
                headers=platform_admin.headers,
                json={"is_active": False},
            ),
            e2e_client.delete(
                f"/api/roles/{PLATFORM_ADMIN_ROLE_ID}/users/{platform_admin.user_id}",
                headers=platform_admin.headers,
            ),
            e2e_client.delete(
                f"/api/users/{platform_admin.user_id}",
                headers=platform_admin.headers,
            ),
        ]
        assert all(response.status_code == 409 for response in responses)
        assert all("active person" in response.json()["detail"] for response in responses)
    finally:
        for row in rows:
            await db_session.execute(
                update(UserORM).where(UserORM.id == row.id).values(is_active=row.is_active)
            )
        await db_session.commit()


@pytest.mark.e2e
def test_external_role_guards_apply_to_rest_mutations(e2e_client, platform_admin, org1):
    """External people cannot receive, retain, or inherit privileged access."""
    user_ids: list[str] = []
    role_ids: list[str] = []

    def create_user(*, external: bool) -> str:
        response = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": f"contoso-{uuid4().hex[:8]}@example.com",
                "name": "Contoso User",
                "organization_id": org1["id"],
                "is_external": external,
            },
        )
        assert response.status_code == 201, response.text
        user_ids.append(response.json()["id"])
        return user_ids[-1]

    def create_role() -> str:
        response = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": f"Contoso Role {uuid4().hex[:8]}", "description": "Test role"},
        )
        assert response.status_code == 201, response.text
        role_ids.append(response.json()["id"])
        return role_ids[-1]

    try:
        external = create_user(external=True)
        plain_role = create_role()
        assert e2e_client.post(
            f"/api/roles/{plain_role}/users",
            headers=platform_admin.headers,
            json={"user_ids": [external]},
        ).status_code == 204
        permission = e2e_client.put(
            f"/api/roles/{plain_role}/permissions",
            headers=platform_admin.headers,
            json={"permissions": ["users.readwrite"]},
        )
        assert permission.status_code == 409

        privileged_role = create_role()
        assert e2e_client.put(
            f"/api/roles/{privileged_role}/permissions",
            headers=platform_admin.headers,
            json={"permissions": ["users.readwrite"]},
        ).status_code == 200
        assignment = e2e_client.post(
            f"/api/roles/{privileged_role}/users",
            headers=platform_admin.headers,
            json={"user_ids": [external]},
        )
        assert assignment.status_code == 409

        internal = create_user(external=False)
        assert e2e_client.post(
            f"/api/roles/{privileged_role}/users",
            headers=platform_admin.headers,
            json={"user_ids": [internal]},
        ).status_code == 204
        external_transition = e2e_client.patch(
            f"/api/users/{internal}",
            headers=platform_admin.headers,
            json={"is_external": True},
        )
        assert external_transition.status_code == 409
    finally:
        for user_id in user_ids:
            e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)
        for role_id in role_ids:
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestDisableEnableUser:
    """Test disabling and re-enabling users."""

    def test_disable_and_reenable_user(self, e2e_client, platform_admin, org1):
        """Admin can disable and then re-enable a user via PATCH."""
        # Create a test user
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "toggle-test@gobifrost.dev",
                "name": "Toggle Test",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        # Disable the user
        disable_resp = e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        assert disable_resp.status_code == 200
        assert disable_resp.json()["is_active"] is False

        # Re-enable the user
        enable_resp = e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": True},
        )
        assert enable_resp.status_code == 200
        assert enable_resp.json()["is_active"] is True

        # Cleanup: disable then delete
        e2e_client.patch(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        e2e_client.delete(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestPermanentDelete:
    """Test permanent user deletion."""

    def test_can_permanently_delete_active_user(self, e2e_client, platform_admin, org1):
        """Can permanently delete a user without disabling first."""
        # Create a test user (active by default)
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "permadelete@gobifrost.dev",
                "name": "Perma Delete",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        # Delete directly (no disable step needed)
        del_resp = e2e_client.delete(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
        )
        assert del_resp.status_code == 204

        # Verify user is gone
        list_resp = e2e_client.get(
            "/api/users",
            headers=platform_admin.headers,
            params={"include_inactive": "true"},
        )
        assert list_resp.status_code == 200
        user_ids = [u["id"] for u in list_resp.json()]
        assert user_id not in user_ids

    def test_can_delete_user_with_role_assignments(self, e2e_client, platform_admin, org1):
        """Deleting a user with role assignments cleans up the assignments."""
        # Create a test user
        create_resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": "delete-with-roles@gobifrost.dev",
                "name": "Delete With Roles",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        # Create a role and assign the user to it
        role_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": "Temp Role For Delete Test", "description": "test"},
        )
        assert role_resp.status_code == 201
        role_id = role_resp.json()["id"]

        assign_resp = e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=platform_admin.headers,
            json={"user_ids": [user_id]},
        )
        assert assign_resp.status_code == 204

        # Delete the user — should succeed despite role assignments
        del_resp = e2e_client.delete(
            f"/api/users/{user_id}",
            headers=platform_admin.headers,
        )
        assert del_resp.status_code == 204

        # Verify role still exists but user is gone from it
        role_users_resp = e2e_client.get(
            f"/api/roles/{role_id}/users",
            headers=platform_admin.headers,
        )
        assert role_users_resp.status_code == 200
        assert user_id not in role_users_resp.json().get("user_ids", [])

        # Cleanup: delete the role
        e2e_client.delete(
            f"/api/roles/{role_id}",
            headers=platform_admin.headers,
        )
