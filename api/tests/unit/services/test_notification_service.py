"""
Unit tests for Notification Service.

Tests the Redis-based notification management and WebSocket delivery.
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock


class TestNotificationService:
    """Tests for NotificationService."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis instance."""
        redis = AsyncMock()
        redis.setex = AsyncMock()
        redis.get = AsyncMock()
        redis.delete = AsyncMock()
        redis.sadd = AsyncMock(return_value=1)
        redis.srem = AsyncMock(return_value=1)
        redis.smembers = AsyncMock(return_value=set())
        redis.sismember = AsyncMock(return_value=False)
        redis.expire = AsyncMock(return_value=True)
        redis.aclose = AsyncMock()
        return redis

    @pytest.fixture
    def mock_pubsub(self):
        """Create a mock pubsub manager."""
        pubsub = MagicMock()
        pubsub.broadcast = AsyncMock()
        return pubsub

    @pytest.fixture
    def notification_service(self, mock_redis, mock_pubsub):
        """Create a notification service with mocked dependencies."""
        from src.services.notification_service import NotificationService

        service = NotificationService()
        service._redis = mock_redis

        # Patch the pubsub manager
        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            yield service, mock_pubsub

    async def test_create_notification_basic(self, notification_service, mock_redis):
        """Test creating a basic notification."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationCreate,
            NotificationStatus,
        )

        service, mock_pubsub = notification_service

        request = NotificationCreate(
            category=NotificationCategory.GITHUB_SETUP,
            title="Setting up GitHub",
            description="Starting repository clone...",
        )

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            notification = await service.create_notification(
                user_id="user-123",
                request=request,
            )

        assert notification.id is not None
        assert notification.category == NotificationCategory.GITHUB_SETUP
        assert notification.title == "Setting up GitHub"
        assert notification.description == "Starting repository clone..."
        assert notification.status == NotificationStatus.PENDING
        assert notification.user_id == "user-123"
        assert notification.percent is None
        assert notification.error is None

        # Verify Redis calls
        mock_redis.setex.assert_called_once()
        mock_redis.sadd.assert_called_once()
        mock_redis.expire.assert_called()

        # Verify WebSocket broadcast
        mock_pubsub.broadcast.assert_called_once()
        call_args = mock_pubsub.broadcast.call_args
        assert call_args[0][0] == "notification:user-123"
        assert call_args[0][1]["type"] == "notification_created"

    async def test_create_notification_with_percent(self, notification_service, mock_redis):
        """Test creating a notification with progress percentage."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationCreate,
        )

        service, mock_pubsub = notification_service

        request = NotificationCreate(
            category=NotificationCategory.FILE_UPLOAD,
            title="Uploading files",
            description="Uploading file.txt...",
            percent=25.0,
        )

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            notification = await service.create_notification(
                user_id="user-123",
                request=request,
            )

        assert notification.percent == 25.0

    async def test_create_notification_for_admins(self, notification_service, mock_redis):
        """Test creating a notification that also goes to admins."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationCreate,
        )

        service, mock_pubsub = notification_service

        request = NotificationCreate(
            category=NotificationCategory.SYSTEM,
            title="System Alert",
            description="Important system notification",
        )

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            _notification = await service.create_notification(
                user_id="user-123",
                request=request,
                for_admins=True,
            )

        # Verify added to admin set
        assert mock_redis.sadd.call_count == 2  # User set + admin set

        # Verify admin channel broadcast
        assert mock_pubsub.broadcast.call_count == 2
        channels = [call[0][0] for call in mock_pubsub.broadcast.call_args_list]
        assert "notification:user-123" in channels
        assert "notification:admins" in channels

    async def test_update_notification_status(self, notification_service, mock_redis):
        """Test updating notification status."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
            NotificationUpdate,
        )

        service, mock_pubsub = notification_service

        # Set up existing notification
        existing = {
            "id": "notif-123",
            "category": NotificationCategory.GITHUB_SETUP.value,
            "title": "Setting up GitHub",
            "description": "Starting...",
            "status": NotificationStatus.PENDING.value,
            "percent": None,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": "user-123",
        }
        mock_redis.get.return_value = json.dumps(existing)
        mock_redis.sismember.return_value = False

        update = NotificationUpdate(
            status=NotificationStatus.RUNNING,
            description="Cloning repository...",
            percent=50.0,
        )

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            result = await service.update_notification(
                notification_id="notif-123",
                update=update,
            )

        assert result is not None
        assert result.status == NotificationStatus.RUNNING
        assert result.description == "Cloning repository..."
        assert result.percent == 50.0

        # Verify WebSocket broadcast
        mock_pubsub.broadcast.assert_called_once()
        call_args = mock_pubsub.broadcast.call_args
        assert call_args[0][1]["type"] == "notification_updated"

    async def test_update_notification_completed(self, notification_service, mock_redis):
        """Test updating notification to completed status changes TTL."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
            NotificationUpdate,
        )
        from src.services.notification_service import (
            COMPLETED_NOTIFICATION_TTL,
        )

        service, mock_pubsub = notification_service

        # Set up existing notification
        existing = {
            "id": "notif-123",
            "category": NotificationCategory.GITHUB_SETUP.value,
            "title": "Setting up GitHub",
            "description": "Almost done...",
            "status": NotificationStatus.RUNNING.value,
            "percent": 90.0,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": "user-123",
        }
        mock_redis.get.return_value = json.dumps(existing)
        mock_redis.sismember.return_value = False

        update = NotificationUpdate(
            status=NotificationStatus.COMPLETED,
            description="Setup complete!",
            percent=100.0,
            result={"repo_url": "https://github.com/test/repo"},
        )

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            result = await service.update_notification(
                notification_id="notif-123",
                update=update,
            )

        assert result.status == NotificationStatus.COMPLETED
        assert result.result == {"repo_url": "https://github.com/test/repo"}

        # Verify TTL was set to completed TTL
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == COMPLETED_NOTIFICATION_TTL

    async def test_update_notification_failed(self, notification_service, mock_redis):
        """Test updating notification to failed status with error."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
            NotificationUpdate,
        )

        service, mock_pubsub = notification_service

        # Set up existing notification
        existing = {
            "id": "notif-123",
            "category": NotificationCategory.GITHUB_SETUP.value,
            "title": "Setting up GitHub",
            "description": "Cloning...",
            "status": NotificationStatus.RUNNING.value,
            "percent": 30.0,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": "user-123",
        }
        mock_redis.get.return_value = json.dumps(existing)
        mock_redis.sismember.return_value = False

        update = NotificationUpdate(
            status=NotificationStatus.FAILED,
            error="Failed to clone repository: Authentication failed",
        )

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            result = await service.update_notification(
                notification_id="notif-123",
                update=update,
            )

        assert result.status == NotificationStatus.FAILED
        assert result.error == "Failed to clone repository: Authentication failed"

    async def test_update_notification_can_clear_retry_state(
        self,
        notification_service,
        mock_redis,
    ):
        """Explicit nulls clear stale error/result while omitted fields survive."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
            NotificationUpdate,
        )

        service, mock_pubsub = notification_service
        existing = {
            "id": "notif-123",
            "category": NotificationCategory.SYSTEM.value,
            "title": "Durable job",
            "description": "Retrying",
            "status": NotificationStatus.PENDING.value,
            "percent": 40.0,
            "error": "Runner stopped",
            "result": {"partial": True},
            "metadata": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": "user-123",
        }
        mock_redis.get.return_value = json.dumps(existing)
        mock_redis.sismember.return_value = False

        update = NotificationUpdate(
            status=NotificationStatus.RUNNING,
            error=None,
            result=None,
            percent=None,
        )
        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            result = await service.update_notification("notif-123", update)

        assert result is not None
        assert result.status == NotificationStatus.RUNNING
        assert result.description == "Retrying"
        assert result.error is None
        assert result.result is None
        assert result.percent is None

    async def test_update_notification_not_found(self, notification_service, mock_redis):
        """Test updating non-existent notification returns None."""
        from src.models.contracts.notifications import NotificationUpdate, NotificationStatus

        service, mock_pubsub = notification_service

        mock_redis.get.return_value = None

        update = NotificationUpdate(status=NotificationStatus.RUNNING)

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            result = await service.update_notification(
                notification_id="notif-nonexistent",
                update=update,
            )

        assert result is None

    async def test_get_notification_exists(self, notification_service, mock_redis):
        """Test getting existing notification."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
        )

        service, _ = notification_service

        existing = {
            "id": "notif-123",
            "category": NotificationCategory.GITHUB_SETUP.value,
            "title": "Setting up GitHub",
            "description": "Complete",
            "status": NotificationStatus.COMPLETED.value,
            "percent": 100.0,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": "user-123",
        }
        mock_redis.get.return_value = json.dumps(existing)

        result = await service.get_notification("notif-123")

        assert result is not None
        assert result.id == "notif-123"
        assert result.title == "Setting up GitHub"

    async def test_get_notification_not_exists(self, notification_service, mock_redis):
        """Test getting non-existent notification returns None."""
        service, _ = notification_service

        mock_redis.get.return_value = None

        result = await service.get_notification("notif-nonexistent")

        assert result is None

    async def test_dismiss_notification_success(self, notification_service, mock_redis):
        """Test dismissing own notification."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
        )

        service, mock_pubsub = notification_service

        existing = {
            "id": "notif-123",
            "category": NotificationCategory.GITHUB_SETUP.value,
            "title": "Setting up GitHub",
            "description": "Complete",
            "status": NotificationStatus.COMPLETED.value,
            "percent": 100.0,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": "user-123",
        }
        mock_redis.get.return_value = json.dumps(existing)

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            result = await service.dismiss_notification(
                notification_id="notif-123",
                user_id="user-123",
            )

        assert result is True
        mock_redis.delete.assert_called_once()
        mock_redis.srem.assert_called()

        # Verify dismissal broadcast
        mock_pubsub.broadcast.assert_called_once()
        call_args = mock_pubsub.broadcast.call_args
        assert call_args[0][1]["type"] == "notification_dismissed"
        assert call_args[0][1]["notification_id"] == "notif-123"

    async def test_dismiss_notification_not_owner(self, notification_service, mock_redis):
        """Test dismissing notification owned by another user fails."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
        )

        service, mock_pubsub = notification_service

        existing = {
            "id": "notif-123",
            "category": NotificationCategory.GITHUB_SETUP.value,
            "title": "Setting up GitHub",
            "description": "Complete",
            "status": NotificationStatus.COMPLETED.value,
            "percent": 100.0,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": "other-user",
        }
        mock_redis.get.return_value = json.dumps(existing)

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            result = await service.dismiss_notification(
                notification_id="notif-123",
                user_id="user-123",
            )

        assert result is False
        mock_redis.delete.assert_not_called()

    async def test_dismiss_notification_not_found(self, notification_service, mock_redis):
        """Test dismissing non-existent notification returns False."""
        service, mock_pubsub = notification_service

        mock_redis.get.return_value = None

        with patch("src.services.notification_service.pubsub_manager", mock_pubsub):
            result = await service.dismiss_notification(
                notification_id="notif-nonexistent",
                user_id="user-123",
            )

        assert result is False

    async def test_get_user_notifications(self, notification_service, mock_redis):
        """Test getting all notifications for a user."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
        )

        service, _ = notification_service

        # Set up user's notification IDs
        mock_redis.smembers.return_value = {"notif-1", "notif-2"}

        # Set up notification data
        notif1 = {
            "id": "notif-1",
            "category": NotificationCategory.GITHUB_SETUP.value,
            "title": "GitHub Setup 1",
            "description": None,
            "status": NotificationStatus.COMPLETED.value,
            "percent": 100.0,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": "2024-01-01T10:00:00+00:00",
            "updated_at": "2024-01-01T10:05:00+00:00",
            "user_id": "user-123",
        }
        notif2 = {
            "id": "notif-2",
            "category": NotificationCategory.FILE_UPLOAD.value,
            "title": "File Upload",
            "description": None,
            "status": NotificationStatus.RUNNING.value,
            "percent": 50.0,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": "2024-01-01T11:00:00+00:00",
            "updated_at": "2024-01-01T11:02:00+00:00",
            "user_id": "user-123",
        }

        async def get_side_effect(key):
            if "notif-1" in key:
                return json.dumps(notif1)
            elif "notif-2" in key:
                return json.dumps(notif2)
            return None

        mock_redis.get.side_effect = get_side_effect

        result = await service.get_user_notifications("user-123")

        assert len(result) == 2
        # Should be sorted by created_at (newest first)
        assert result[0].id == "notif-2"
        assert result[1].id == "notif-1"

    async def test_get_user_notifications_includes_admin(self, notification_service, mock_redis):
        """Test getting notifications includes admin notifications when requested."""
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationStatus,
        )

        service, _ = notification_service

        # Mock smembers to return different sets for user and admin
        async def smembers_side_effect(key):
            if "user:" in key:
                return {"notif-user"}
            else:
                return {"notif-admin"}

        mock_redis.smembers.side_effect = smembers_side_effect

        notif_user = {
            "id": "notif-user",
            "category": NotificationCategory.FILE_UPLOAD.value,
            "title": "User Upload",
            "description": None,
            "status": NotificationStatus.COMPLETED.value,
            "percent": 100.0,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": "2024-01-01T10:00:00+00:00",
            "updated_at": "2024-01-01T10:05:00+00:00",
            "user_id": "user-123",
        }
        notif_admin = {
            "id": "notif-admin",
            "category": NotificationCategory.SYSTEM.value,
            "title": "System Alert",
            "description": None,
            "status": NotificationStatus.COMPLETED.value,
            "percent": None,
            "error": None,
            "result": None,
            "metadata": None,
            "created_at": "2024-01-01T11:00:00+00:00",
            "updated_at": "2024-01-01T11:00:00+00:00",
            "user_id": "admin-user",
        }

        async def get_side_effect(key):
            if "notif-user" in key:
                return json.dumps(notif_user)
            elif "notif-admin" in key:
                return json.dumps(notif_admin)
            return None

        mock_redis.get.side_effect = get_side_effect

        result = await service.get_user_notifications("user-123", include_admin=True)

        assert len(result) == 2
        ids = [n.id for n in result]
        assert "notif-user" in ids
        assert "notif-admin" in ids

    async def test_close(self, notification_service, mock_redis):
        """Test closing Redis connection."""
        service, _ = notification_service

        await service.close()

        mock_redis.aclose.assert_called_once()
        assert service._redis is None


class TestNotificationServiceSingleton:
    """Tests for notification service singleton functions."""

    def test_get_notification_service_returns_singleton(self):
        """Test that get_notification_service returns same instance."""
        import src.services.notification_service as module

        # Reset singleton
        module._notification_service = None

        from src.services.notification_service import get_notification_service

        service1 = get_notification_service()
        service2 = get_notification_service()

        assert service1 is service2

        # Cleanup
        module._notification_service = None

    async def test_close_notification_service(self):
        """Test closing singleton notification service."""
        import src.services.notification_service as module

        # Reset singleton
        module._notification_service = None

        from src.services.notification_service import (
            get_notification_service,
            close_notification_service,
        )

        get_notification_service()  # Creates singleton
        assert module._notification_service is not None

        await close_notification_service()
        assert module._notification_service is None
