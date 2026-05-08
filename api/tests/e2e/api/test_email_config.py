"""
E2E tests for email workflow configuration endpoints.

Tests admin endpoints for configuring a workflow as the email provider.
"""

import pytest


@pytest.mark.e2e
class TestEmailConfigEndpoints:
    """Test email configuration admin endpoints."""

    def test_get_config_returns_none_when_unconfigured(
        self, e2e_client, platform_admin
    ):
        """GET /api/admin/email/config returns null when not configured."""
        response = e2e_client.get(
            "/api/admin/email/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json() is None

    def test_set_config_rejects_invalid_workflow(self, e2e_client, platform_admin):
        """POST /api/admin/email/config rejects non-existent workflow."""
        response = e2e_client.post(
            "/api/admin/email/config",
            json={"workflow_id": "00000000-0000-0000-0000-000000000000"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 422
        assert "not found" in response.json()["detail"].lower()

    def test_validate_rejects_invalid_workflow(self, e2e_client, platform_admin):
        """POST /api/admin/email/validate/{id} rejects non-existent workflow."""
        response = e2e_client.post(
            "/api/admin/email/validate/00000000-0000-0000-0000-000000000000",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert "not found" in data["message"].lower()
        assert data["email_sent"] is False

    def test_validate_with_recipient_does_not_send_when_invalid(
        self, e2e_client, platform_admin
    ):
        """Validate with recipient on a missing workflow short-circuits (no send attempt)."""
        response = e2e_client.post(
            "/api/admin/email/validate/00000000-0000-0000-0000-000000000000",
            json={"recipient": "test@example.com"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert data["email_sent"] is False
        assert data["execution_id"] is None

    def test_validate_with_no_body_does_not_send(self, e2e_client, platform_admin):
        """Validate with no body keeps backward-compat behavior (validation only)."""
        response = e2e_client.post(
            "/api/admin/email/validate/00000000-0000-0000-0000-000000000000",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email_sent"] is False

    def test_delete_config_returns_404_when_not_exists(
        self, e2e_client, platform_admin
    ):
        """DELETE /api/admin/email/config returns 404 when not configured."""
        response = e2e_client.delete(
            "/api/admin/email/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


@pytest.mark.e2e
class TestEmailConfigPermissions:
    """Test email configuration permission requirements."""

    def test_get_config_requires_platform_admin(self, e2e_client, org1_user):
        """GET /api/admin/email/config requires platform admin."""
        response = e2e_client.get(
            "/api/admin/email/config",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_set_config_requires_platform_admin(self, e2e_client, org1_user):
        """POST /api/admin/email/config requires platform admin."""
        response = e2e_client.post(
            "/api/admin/email/config",
            json={"workflow_id": "00000000-0000-0000-0000-000000000000"},
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_delete_config_requires_platform_admin(self, e2e_client, org1_user):
        """DELETE /api/admin/email/config requires platform admin."""
        response = e2e_client.delete(
            "/api/admin/email/config",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_validate_requires_platform_admin(self, e2e_client, org1_user):
        """POST /api/admin/email/validate/{id} requires platform admin."""
        response = e2e_client.post(
            "/api/admin/email/validate/00000000-0000-0000-0000-000000000000",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_get_config_requires_auth(self, e2e_client):
        """GET /api/admin/email/config requires authentication."""
        response = e2e_client.get("/api/admin/email/config")
        # Returns 403 (forbidden) when no auth due to RequirePlatformAdmin dependency
        assert response.status_code in (401, 403)
