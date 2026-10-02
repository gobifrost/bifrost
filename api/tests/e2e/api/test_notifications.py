"""
E2E tests for Notification API.

Tests the notifications REST endpoints:
- List notifications
- Dismiss notifications
- Lock status checking
- Authorization (users can only see their own notifications)
"""

import pytest


@pytest.mark.e2e
class TestNotificationAPI:
    """Test notification REST API endpoints."""

    def test_get_notifications_empty(self, e2e_client, platform_admin):
        """Test getting notifications when there are none."""
        response = e2e_client.get(
            "/api/notifications",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get notifications failed: {response.text}"

        data = response.json()
        assert "notifications" in data
        assert isinstance(data["notifications"], list)

    def test_get_notification_not_found(self, e2e_client, platform_admin):
        """Test getting a non-existent notification."""
        response = e2e_client.get(
            "/api/notifications/non-existent-id",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_dismiss_notification_not_found(self, e2e_client, platform_admin):
        """Test dismissing a non-existent notification."""
        response = e2e_client.delete(
            "/api/notifications/non-existent-id",
            headers=platform_admin.headers,
        )
        # Should return 404 since notification doesn't exist
        assert response.status_code == 404


@pytest.mark.e2e
class TestUploadLockAPI:
    """Test upload lock status API endpoints."""

    def test_get_upload_lock_status_unlocked(self, e2e_client, platform_admin):
        """Test checking upload lock when not locked."""
        response = e2e_client.get(
            "/api/notifications/locks/upload",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get lock status failed: {response.text}"

        data = response.json()
        assert "locked" in data
        # May or may not be locked depending on other tests
        assert isinstance(data["locked"], bool)

    def test_force_release_upload_lock_not_locked(self, e2e_client, platform_admin):
        """Test force releasing upload lock when not locked."""
        response = e2e_client.delete(
            "/api/notifications/locks/upload",
            headers=platform_admin.headers,
        )
        # Should succeed but indicate nothing was released
        assert response.status_code in [200, 404]


@pytest.mark.e2e
class TestNotificationAuthorization:
    """Test notification authorization - users can only see their own."""

    def test_org_user_get_notifications(self, e2e_client, org1_user):
        """Test that org users can access their own notifications."""
        response = e2e_client.get(
            "/api/notifications",
            headers=org1_user.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "notifications" in data

    def test_org_user_cannot_see_lock_status(self, e2e_client, org1_user):
        """Test that org users cannot check upload lock status (admin only)."""
        response = e2e_client.get(
            "/api/notifications/locks/upload",
            headers=org1_user.headers,
        )
        # Should be forbidden for non-platform users
        assert response.status_code in [403, 404]

    def test_org_user_cannot_force_release_lock(self, e2e_client, org1_user):
        """Test that org users cannot force release locks."""
        response = e2e_client.delete(
            "/api/notifications/locks/upload",
            headers=org1_user.headers,
        )
        # Should be forbidden for non-platform users
        assert response.status_code in [403, 404]

    def test_unauthenticated_cannot_access_notifications(self, e2e_client):
        """Test that unauthenticated requests are rejected."""
        response = e2e_client.get("/api/notifications")
        assert response.status_code in [401, 403, 422]
