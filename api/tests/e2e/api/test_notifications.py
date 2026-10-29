"""
E2E tests for Notification API.

Tests the notifications REST endpoints:
- List notifications
- Dismiss notifications
- Lock status checking
- Authorization (users can only see their own notifications)
"""

import asyncio
from uuid import uuid4

import pytest

from src.models.contracts.notifications import NotificationCategory, NotificationCreate
from src.services.notification_service import NotificationService


def _create_admin_notification() -> str:
    async def create() -> str:
        service = NotificationService()
        try:
            notification = await service.create_notification(
                user_id="system",
                request=NotificationCreate(
                    category=NotificationCategory.SYSTEM,
                    title=f"Admin notification {uuid4()}",
                ),
                for_admins=True,
            )
            return notification.id
        finally:
            await service.close()

    return asyncio.run(create())


def _dismiss_notification_as_owner(notification_id: str, user_id: str) -> None:
    async def dismiss() -> None:
        service = NotificationService()
        try:
            await service.dismiss_notification(notification_id, user_id)
        finally:
            await service.close()

    asyncio.run(dismiss())


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

    def test_org_user_gets_own_notification_detail(self, e2e_client, org1_user):
        """Personal detail remains readable by its owner without an admin grant."""
        async def create() -> str:
            service = NotificationService()
            try:
                notification = await service.create_notification(
                    user_id=str(org1_user.user_id),
                    request=NotificationCreate(
                        category=NotificationCategory.SYSTEM,
                        title=f"Own notification {uuid4()}",
                    ),
                )
                return notification.id
            finally:
                await service.close()

        notification_id = asyncio.run(create())
        try:
            response = e2e_client.get(
                f"/api/notifications/{notification_id}", headers=org1_user.headers
            )
            assert response.status_code == 200
        finally:
            _dismiss_notification_as_owner(notification_id, str(org1_user.user_id))

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

    def test_only_admins_dismiss_admin_notifications(self, e2e_client, platform_admin, org1_user):
        """Dismissing an admin notification follows the same rule as reading it."""
        notification_id = _create_admin_notification()
        path = f"/api/notifications/{notification_id}"

        user_read = e2e_client.get(path, headers=org1_user.headers)
        user_dismiss = e2e_client.delete(path, headers=org1_user.headers)
        admin_read = e2e_client.get(path, headers=platform_admin.headers)

        assert user_read.status_code == 404
        assert user_dismiss.status_code == 404
        assert admin_read.status_code == 200

        admin_dismiss = e2e_client.delete(path, headers=platform_admin.headers)
        admin_read_after = e2e_client.get(path, headers=platform_admin.headers)

        assert admin_dismiss.status_code == 204
        assert admin_read_after.status_code == 404

    def test_admin_cannot_read_another_users_personal_notification(self, e2e_client, platform_admin):
        """Superuser admission never makes a personal notification globally readable."""
        async def create() -> str:
            service = NotificationService()
            try:
                notification = await service.create_notification(
                    user_id="another-person",
                    request=NotificationCreate(
                        category=NotificationCategory.SYSTEM,
                        title=f"Personal notification {uuid4()}",
                    ),
                )
                return notification.id
            finally:
                await service.close()

        notification_id = asyncio.run(create())
        try:
            response = e2e_client.get(
                f"/api/notifications/{notification_id}", headers=platform_admin.headers
            )
            assert response.status_code == 404
        finally:
            _dismiss_notification_as_owner(notification_id, "another-person")

    def test_unauthenticated_cannot_access_notifications(self, e2e_client):
        """Test that unauthenticated requests are rejected."""
        response = e2e_client.get("/api/notifications")
        assert response.status_code in [401, 403, 422]
