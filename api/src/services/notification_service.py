"""
Notification Service

Manages ephemeral notifications stored in Redis with WebSocket delivery.
Supports progress notifications for long-running operations.

Redis Key Structure:
- bifrost:notification:{notification_id} - Individual notification data
- bifrost:notifications:user:{user_id} - Set of notification IDs for a user
- bifrost:notifications:admins - Set of notification IDs for platform admins
"""

import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, cast
from uuid import uuid4

import redis.asyncio as redis

from src.config import get_settings
from src.core.log_safety import log_safe
from src.core.pubsub import manager as pubsub_manager
from src.models.contracts.notifications import (
    NotificationCategory,
    NotificationCreate,
    NotificationPublic,
    NotificationStatus,
    NotificationUpdate,
)

logger = logging.getLogger(__name__)

# Redis key prefixes
NOTIFICATION_KEY_PREFIX = "bifrost:notification:"
USER_NOTIFICATIONS_PREFIX = "bifrost:notifications:user:"
ADMIN_NOTIFICATIONS_KEY = "bifrost:notifications:admins"

# TTL settings
ACTIVE_NOTIFICATION_TTL = 3600  # 1 hour for active (pending/running)
COMPLETED_NOTIFICATION_TTL = 300  # 5 minutes after done


class NotificationService:
    """
    Service for managing ephemeral notifications.

    Provides:
    - Create/update notifications with WebSocket delivery
    - Scoped delivery to individual users or platform admins
    - Redis-based ephemeral storage with TTL
    """

    def __init__(self):
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            settings = get_settings()
            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    # =========================================================================
    # Create/Update/Get Methods
    # =========================================================================

    async def create_notification(
        self,
        user_id: str,
        request: NotificationCreate,
        for_admins: bool = False,
        initial_status: NotificationStatus = NotificationStatus.PENDING,
    ) -> NotificationPublic:
        """
        Create a new notification.

        Args:
            user_id: User ID who owns the notification
            request: Notification creation request
            for_admins: If True, notify all platform admins
            initial_status: Initial status (defaults to PENDING)

        Returns:
            Created notification
        """
        redis_client = await self._get_redis()
        notification_id = str(uuid4())
        now = datetime.now(timezone.utc)

        notification = NotificationPublic(
            id=notification_id,
            category=request.category,
            title=request.title,
            description=request.description,
            status=initial_status,
            percent=request.percent,
            error=None,
            result=None,
            metadata=request.metadata,
            created_at=now,
            updated_at=now,
            user_id=user_id,
        )

        # Store notification
        key = f"{NOTIFICATION_KEY_PREFIX}{notification_id}"
        await redis_client.setex(
            key,
            ACTIVE_NOTIFICATION_TTL,
            notification.model_dump_json(),
        )

        # Add to user's notification set
        # Cast needed: redis-py returns Union[Awaitable[int], int] but we're async
        user_key = f"{USER_NOTIFICATIONS_PREFIX}{user_id}"
        await cast(Awaitable[int], redis_client.sadd(user_key, notification_id))
        await cast(Awaitable[bool], redis_client.expire(user_key, ACTIVE_NOTIFICATION_TTL))

        # Add to admin set if needed
        if for_admins:
            await cast(Awaitable[int], redis_client.sadd(ADMIN_NOTIFICATIONS_KEY, notification_id))
            await cast(Awaitable[bool], redis_client.expire(ADMIN_NOTIFICATIONS_KEY, ACTIVE_NOTIFICATION_TTL))

        # Publish via WebSocket
        await self._publish_notification(
            user_id=user_id,
            notification=notification,
            event_type="notification_created",
            for_admins=for_admins,
        )

        logger.info(f"Created notification: {notification_id} for user {user_id}")
        return notification

    async def update_notification(
        self,
        notification_id: str,
        update: NotificationUpdate,
    ) -> NotificationPublic | None:
        """
        Update an existing notification.

        Args:
            notification_id: Notification ID to update
            update: Update data

        Returns:
            Updated notification or None if not found
        """
        redis_client = await self._get_redis()
        key = f"{NOTIFICATION_KEY_PREFIX}{notification_id}"

        # Get existing notification
        data = await redis_client.get(key)
        if data is None:
            logger.warning(f"Notification not found: {notification_id}")
            return None

        notification_dict = json.loads(data)
        now = datetime.now(timezone.utc)

        # Update fields
        if update.status is not None:
            notification_dict["status"] = update.status.value

        # Nullable fields use Pydantic's explicit-field set so callers can
        # clear stale progress/error/result values during a durable-job retry.
        # An omitted field still means "leave unchanged".
        if "description" in update.model_fields_set:
            notification_dict["description"] = update.description

        if "percent" in update.model_fields_set:
            notification_dict["percent"] = update.percent

        if "error" in update.model_fields_set:
            notification_dict["error"] = update.error

        if "result" in update.model_fields_set:
            notification_dict["result"] = update.result

        notification_dict["updated_at"] = now.isoformat()

        # Determine TTL based on status
        is_complete = notification_dict["status"] in [
            NotificationStatus.COMPLETED.value,
            NotificationStatus.FAILED.value,
            NotificationStatus.CANCELLED.value,
        ]
        ttl = COMPLETED_NOTIFICATION_TTL if is_complete else ACTIVE_NOTIFICATION_TTL

        # Save updated notification
        await redis_client.setex(key, ttl, json.dumps(notification_dict))

        # Parse and publish
        notification = NotificationPublic.model_validate(notification_dict)

        for_admins = await self._is_admin_notification(notification_id)
        logger.info(
            f"Publishing notification update: {notification_id} "
            f"status={notification.status} user_id={notification.user_id} for_admins={for_admins}"
        )

        await self._publish_notification(
            user_id=notification.user_id,
            notification=notification,
            event_type="notification_updated",
            for_admins=for_admins,
        )

        logger.debug(f"Updated notification: {notification_id}")
        return notification

    async def get_notification(
        self,
        notification_id: str,
    ) -> NotificationPublic | None:
        """Get a notification by ID."""
        redis_client = await self._get_redis()
        key = f"{NOTIFICATION_KEY_PREFIX}{notification_id}"

        data = await redis_client.get(key)
        if data is None:
            return None

        return NotificationPublic.model_validate_json(data)

    async def dismiss_notification(
        self,
        notification_id: str,
        user_id: str,
    ) -> bool:
        """
        Dismiss (delete) a notification.

        Args:
            notification_id: Notification to dismiss
            user_id: User requesting dismissal (must own or be admin for admin notifications)

        Returns:
            True if dismissed, False if not found or unauthorized
        """
        redis_client = await self._get_redis()
        key = f"{NOTIFICATION_KEY_PREFIX}{notification_id}"

        # Get notification to verify ownership
        data = await redis_client.get(key)
        if data is None:
            return False

        notification_dict = json.loads(data)
        owner_id = notification_dict.get("user_id")

        # Allow dismissal if user owns it OR if it's an admin notification
        # (platform admins can dismiss any admin notification they can see)
        is_admin_notif = await self._is_admin_notification(notification_id)
        if owner_id != user_id and not is_admin_notif:
            logger.warning(
                f"User {user_id} attempted to dismiss notification owned by {owner_id}"
            )
            return False

        # Delete notification
        await redis_client.delete(key)

        # Remove from owner's set (use actual owner, not requesting user)
        owner_key = f"{USER_NOTIFICATIONS_PREFIX}{owner_id}"
        await cast(Awaitable[int], redis_client.srem(owner_key, notification_id))

        # Remove from admin set if present
        await cast(Awaitable[int], redis_client.srem(ADMIN_NOTIFICATIONS_KEY, notification_id))

        # Publish dismissal to owner's channel
        await self._publish_dismissal(owner_id, notification_id)

        logger.info(f"Dismissed notification: {log_safe(notification_id)}")
        return True

    async def find_admin_notification_by_title(
        self,
        title: str,
        category: NotificationCategory | None = None,
    ) -> NotificationPublic | None:
        """
        Find existing admin notification by title (and optionally category).

        Used for deduplication - prevents creating duplicate notifications
        for the same event (e.g., maintenance required).

        Args:
            title: Notification title to search for
            category: Optional category to filter by

        Returns:
            Matching notification or None if not found
        """
        redis_client = await self._get_redis()
        admin_ids: set[str] = await cast(
            Awaitable[set[str]], redis_client.smembers(ADMIN_NOTIFICATIONS_KEY)
        )

        for notification_id in admin_ids:
            key = f"{NOTIFICATION_KEY_PREFIX}{notification_id}"
            data = await redis_client.get(key)
            if data is None:
                continue
            try:
                notification = NotificationPublic.model_validate_json(data)
                if notification.title == title:
                    if category is None or notification.category == category:
                        return notification
            except Exception:
                continue

        return None

    # =========================================================================
    # Query Methods
    # =========================================================================

    async def get_user_notifications(
        self,
        user_id: str,
        include_admin: bool = False,
    ) -> list[NotificationPublic]:
        """
        Get all notifications for a user.

        Args:
            user_id: User ID
            include_admin: If True and user is admin, include admin notifications

        Returns:
            List of notifications
        """
        redis_client = await self._get_redis()

        # Get user's notification IDs
        user_key = f"{USER_NOTIFICATIONS_PREFIX}{user_id}"
        notification_ids: set[str] = await cast(
            Awaitable[set[str]], redis_client.smembers(user_key)
        )

        # Include admin notifications if requested
        if include_admin:
            admin_ids: set[str] = await cast(
                Awaitable[set[str]], redis_client.smembers(ADMIN_NOTIFICATIONS_KEY)
            )
            notification_ids = notification_ids.union(admin_ids)

        notifications = []

        for notification_id in notification_ids:
            key = f"{NOTIFICATION_KEY_PREFIX}{notification_id}"
            data = await redis_client.get(key)
            if data is None:
                # Notification expired, clean up set
                await cast(Awaitable[int], redis_client.srem(user_key, notification_id))
                continue

            try:
                notification = NotificationPublic.model_validate_json(data)
                notifications.append(notification)
            except Exception as e:
                logger.warning(f"Failed to parse notification {notification_id}: {e}")

        # Sort by created_at (newest first)
        notifications.sort(key=lambda n: n.created_at, reverse=True)

        return notifications

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def _publish_notification(
        self,
        user_id: str,
        notification: NotificationPublic,
        event_type: str,
        for_admins: bool = False,
    ) -> None:
        """Publish notification update via WebSocket."""
        message = {
            "type": event_type,
            "notification": notification.model_dump(mode="json"),
        }

        # Send to user's notification channel
        await pubsub_manager.broadcast(f"notification:{user_id}", message)

        # Send to admin channel if needed
        if for_admins:
            await pubsub_manager.broadcast("notification:admins", message)

    async def _publish_dismissal(
        self,
        user_id: str,
        notification_id: str,
    ) -> None:
        """Publish notification dismissal via WebSocket."""
        message = {
            "type": "notification_dismissed",
            "notification_id": notification_id,
        }

        await pubsub_manager.broadcast(f"notification:{user_id}", message)

    async def _is_admin_notification(self, notification_id: str) -> bool:
        """Check if notification is in the admin set."""
        redis_client = await self._get_redis()
        result = await cast(
            Awaitable[bool], redis_client.sismember(ADMIN_NOTIFICATIONS_KEY, notification_id)
        )
        return bool(result)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None


# Singleton instance
_notification_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Get singleton notification service instance."""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service


async def close_notification_service() -> None:
    """Close notification service."""
    global _notification_service
    if _notification_service:
        await _notification_service.close()
        _notification_service = None
