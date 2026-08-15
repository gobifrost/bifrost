"""
E2E tests for organization management.

Tests CRUD operations and access control for organizations.
"""

import pytest


@pytest.mark.e2e
class TestOrganizationCRUD:
    """Test organization CRUD operations."""

    def test_organization_created_via_fixture(self, org1):
        """Organization should be created via fixture."""
        assert org1["name"] == "Bifrost Dev Org"
        assert org1["domain"] == "gobifrost.dev"
        assert "id" in org1

    def test_second_organization_created(self, org2):
        """Second organization for isolation tests."""
        assert org2["name"] == "Second Test Org"
        assert org2["domain"] == "org2.gobifrost.com"
        assert "id" in org2

    def test_list_organizations(self, e2e_client, platform_admin, org1, org2):
        """Platform admin can list all organizations."""
        response = e2e_client.get(
            "/api/organizations",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List orgs failed: {response.text}"
        orgs = response.json()
        assert len(orgs) >= 2
        org_names = [o["name"] for o in orgs]
        assert "Bifrost Dev Org" in org_names
        assert "Second Test Org" in org_names

    def test_list_organizations_can_include_inactive(
        self,
        e2e_client,
        platform_admin,
        org2,
    ):
        """Inactive organizations are hidden by default and available on request."""
        update_response = e2e_client.patch(
            f"/api/organizations/{org2['id']}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )
        assert update_response.status_code == 200

        active_response = e2e_client.get(
            "/api/organizations",
            headers=platform_admin.headers,
        )
        assert active_response.status_code == 200
        assert org2["id"] not in {org["id"] for org in active_response.json()}

        all_response = e2e_client.get(
            "/api/organizations",
            params={"include_inactive": True},
            headers=platform_admin.headers,
        )
        assert all_response.status_code == 200
        inactive_org = next(
            org for org in all_response.json() if org["id"] == org2["id"]
        )
        assert inactive_org["is_active"] is False

        restore_response = e2e_client.patch(
            f"/api/organizations/{org2['id']}",
            headers=platform_admin.headers,
            json={"is_active": True},
        )
        assert restore_response.status_code == 200

    def test_get_organization_by_id(self, e2e_client, platform_admin, org1):
        """Platform admin can get specific organization."""
        response = e2e_client.get(
            f"/api/organizations/{org1['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get org failed: {response.text}"
        org = response.json()
        assert org["id"] == org1["id"]
        assert org["name"] == "Bifrost Dev Org"

    def test_provider_organization_cannot_be_disabled(
        self,
        e2e_client,
        platform_admin,
    ):
        """The provider organization remains active when updated directly."""
        list_response = e2e_client.get(
            "/api/organizations",
            headers=platform_admin.headers,
        )
        assert list_response.status_code == 200
        provider = next(org for org in list_response.json() if org["is_provider"])

        response = e2e_client.patch(
            f"/api/organizations/{provider['id']}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Provider organization cannot be disabled"


@pytest.mark.e2e
class TestOrganizationAccess:
    """Test organization access control."""

    def test_org_user_cannot_list_all_organizations(self, e2e_client, org1_user):
        """Org user should not be able to list all organizations."""
        response = e2e_client.get(
            "/api/organizations",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_org_user_cannot_create_organization(self, e2e_client, org1_user):
        """Org user should not be able to create organizations."""
        response = e2e_client.post(
            "/api/organizations",
            headers=org1_user.headers,
            json={"name": "Unauthorized Org", "domain": "unauthorized.com"},
        )
        assert response.status_code == 403


@pytest.mark.e2e
class TestOrganizationIsolation:
    """Test organization isolation."""

    def test_org1_user_only_sees_own_org_data(self, e2e_client, org1_user, org2):
        """Org1 user only sees their own org's resources regardless of query param."""
        # With the new query param approach, org users always see their own org's data
        # The scope param is ignored for non-superusers (they can't filter other orgs)
        response = e2e_client.get(
            "/api/forms",
            params={"scope": org2["id"]},  # Try to filter by org2
            headers=org1_user.headers,
        )
        # Request succeeds but returns org1's forms (query param ignored for org users)
        assert response.status_code == 200
        # Verify no org2 data is returned - all forms should be org1's or global
        forms = response.json()
        for form in forms:
            assert form.get("organization_id") in [None, str(org1_user.organization_id)], \
                f"Org user should only see their own org's forms, not {form.get('organization_id')}"
