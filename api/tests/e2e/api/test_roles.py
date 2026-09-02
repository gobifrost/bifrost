"""
E2E tests for role management.

Tests role CRUD operations and user assignment.
"""

import pytest


@pytest.mark.e2e
class TestRoleCRUD:
    """Test role CRUD operations."""

    @pytest.fixture
    def test_role(self, e2e_client, platform_admin):
        """Create a test role and clean up after."""
        response = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={
                "name": "E2E Test Role",
                "description": "Role for E2E testing",
            },
        )
        assert response.status_code == 201, f"Create role failed: {response.text}"
        role = response.json()

        yield role

        # Cleanup
        e2e_client.delete(
            f"/api/roles/{role['id']}",
            headers=platform_admin.headers,
        )

    def test_create_role(self, e2e_client, platform_admin):
        """Platform admin can create a role."""
        response = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={
                "name": "Form Submitter",
                "description": "Can submit specific forms",
            },
        )
        assert response.status_code == 201, f"Create role failed: {response.text}"
        role = response.json()
        assert role["name"] == "Form Submitter"
        assert "id" in role

        # Cleanup
        e2e_client.delete(
            f"/api/roles/{role['id']}",
            headers=platform_admin.headers,
        )

    def test_list_roles(self, e2e_client, platform_admin, test_role):
        """Users can list roles."""
        response = e2e_client.get(
            "/api/roles",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List roles failed: {response.text}"
        roles = response.json()
        assert isinstance(roles, list)
        role_names = [r["name"] for r in roles]
        assert "E2E Test Role" in role_names

    def test_role_listing_supports_server_pagination_search_and_sort(
        self, e2e_client, platform_admin
    ):
        created = []
        try:
            for name in ["Paged role Alpha", "Paged role Bravo"]:
                response = e2e_client.post(
                    "/api/roles",
                    headers=platform_admin.headers,
                    json={"name": name, "description": "pagination boundary"},
                )
                assert response.status_code == 201, response.text
                created.append(response.json()["id"])

            first = e2e_client.get(
                "/api/roles",
                headers=platform_admin.headers,
                params={
                    "search": "Paged role",
                    "sort_by": "name",
                    "sort_direction": "asc",
                    "limit": 1,
                    "offset": 0,
                },
            )
            assert first.status_code == 200, first.text
            assert first.headers["X-Total-Count"] == "2"
            assert [role["name"] for role in first.json()] == ["Paged role Alpha"]
            assert first.json()[0]["consumer_counts"]["users"] == 0

            second = e2e_client.get(
                "/api/roles",
                headers=platform_admin.headers,
                params={
                    "search": "Paged role",
                    "sort_by": "name",
                    "sort_direction": "asc",
                    "limit": 1,
                    "offset": 1,
                },
            )
            assert [role["name"] for role in second.json()] == ["Paged role Bravo"]
        finally:
            for role_id in created:
                e2e_client.delete(
                    f"/api/roles/{role_id}", headers=platform_admin.headers
                )

    def test_assign_role_to_user(
        self, e2e_client, platform_admin, org1_user, org2_user, test_role
    ):
        """Platform admin can assign role to user."""
        response = e2e_client.post(
            f"/api/roles/{test_role['id']}/users",
            headers=platform_admin.headers,
            json={
                "user_ids": [
                    str(org1_user.user_id),
                    str(org2_user.user_id),
                ]
            },
        )
        # Accept 200, 201, or 204
        assert response.status_code in [200, 201, 204], \
            f"Assign role failed: {response.status_code} - {response.text}"

        assigned = e2e_client.get(
            f"/api/roles/{test_role['id']}/users",
            headers=platform_admin.headers,
            params={"limit": 1, "offset": 0},
        )
        assert assigned.status_code == 200, assigned.text
        first_page = assigned.json()
        assert first_page["total"] == 2
        assert len(first_page["users"]) == 1
        assert first_page["user_ids"] == [first_page["users"][0]["id"]]
        assert first_page["users"][0]["name"] == org1_user.name
        assert first_page["users"][0]["organization_name"] == "Bifrost Dev Org"

        second = e2e_client.get(
            f"/api/roles/{test_role['id']}/users",
            headers=platform_admin.headers,
            params={"limit": 1, "offset": 1},
        )
        assert second.status_code == 200, second.text
        assert second.json()["total"] == 2
        assert second.json()["users"][0]["name"] == org2_user.name

        searched = e2e_client.get(
            f"/api/roles/{test_role['id']}/users",
            headers=platform_admin.headers,
            params={"search": org2_user.email, "limit": 25, "offset": 0},
        )
        assert searched.status_code == 200, searched.text
        assert searched.json()["total"] == 1
        assert searched.json()["users"][0]["email"] == org2_user.email


@pytest.mark.e2e
class TestRoleAccess:
    """Test role access control."""

    def test_org_user_cannot_create_roles(self, e2e_client, org1_user):
        """Org user should not be able to create roles."""
        response = e2e_client.post(
            "/api/roles",
            headers=org1_user.headers,
            json={
                "name": "Unauthorized Role",
                "description": "Should not be created",
            },
        )
        assert response.status_code == 403
