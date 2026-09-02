"""
E2E tests for user management.

Tests user creation, listing, profile operations,
disable/enable, permanent delete, and system user protection.
"""

import pytest

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

    def test_cannot_delete_self(self, e2e_client, platform_admin):
        """Admin cannot delete themselves."""
        response = e2e_client.delete(
            f"/api/users/{platform_admin.user_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 400
        assert "yourself" in response.json()["detail"].lower()


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
