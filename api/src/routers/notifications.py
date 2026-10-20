"""
Notifications Router

Provides REST endpoints for managing notifications and checking upload locks.
Real-time updates are delivered via WebSocket (notification:{user_id} channel).
"""

import logging

from fastapi import APIRouter, HTTPException, status

from shared import access_checks
from src.core.auth import Context, CurrentActiveUser, CurrentUser
from src.core.locks import (
    UPLOAD_LOCK_NAME,
    get_lock_service,
)
from src.models.contracts.notifications import (
    NotificationListResponse,
    NotificationPublic,
    UploadLockInfo,
)
from src.services.authorization.enforce import load_caller, require_permission
from src.services.notification_access import (
    can_access_notification,
    can_read_admin_notifications,
    notification_owner_id,
)
from src.services.notification_service import get_notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


# =============================================================================
# Notification Endpoints
# =============================================================================


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    user: CurrentUser,
    ctx: Context,
) -> NotificationListResponse:
    """
    Get all notifications for the current user.

    Platform admins also receive admin-scoped notifications.

    Returns:
        List of active notifications
    """
    service = get_notification_service()
    caller = await load_caller(ctx.db, user)
    include_admin = can_read_admin_notifications(caller)
    notifications = await service.get_user_notifications(
        user_id=str(user.user_id),
        include_admin=include_admin,
    )
    return NotificationListResponse(notifications=notifications)


@router.get("/{notification_id}", response_model=NotificationPublic)
async def get_notification(
    notification_id: str,
    user: CurrentUser,
    ctx: Context,
) -> NotificationPublic:
    """
    Get a specific notification by ID.

    Args:
        notification_id: Notification ID

    Returns:
        Notification details

    Raises:
        404 if not found or not owned by user
    """
    service = get_notification_service()
    notification = await service.get_notification(notification_id)

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    caller = await load_caller(ctx.db, user)
    owner_id = notification_owner_id(caller)
    if not await can_access_notification(
        service, caller, notification_id, notification
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    if notification.user_id != owner_id:
        access_checks.note_power("platform.read", None, subject=f"notification:{notification_id}")

    return notification


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_notification(
    notification_id: str,
    user: CurrentUser,
    ctx: Context,
) -> None:
    """
    Dismiss (delete) a notification.

    Only the owner can dismiss their notification. Platform admins can also
    dismiss admin-scoped notifications, which they can read.

    For embedding-reindex notifications that are still running, this also sets
    the Redis cancellation flag the scheduler polls between batches — so the
    reindex job stops cleanly with partial state rather than running to
    completion after the notification is gone.

    Args:
        notification_id: Notification ID to dismiss

    Raises:
        404 if not found or not owned by user
    """
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationStatus,
    )
    from src.services.embeddings.reindex import mark_cancelled

    service = get_notification_service()
    caller = await load_caller(ctx.db, user)
    owner_id = notification_owner_id(caller)

    # Check if this is a running embedding-reindex BEFORE dismissing —
    # we need the category and status to decide whether to set the cancel flag.
    notification = await service.get_notification(notification_id)
    if notification is None or not await can_access_notification(
        service, caller, notification_id, notification, write=True
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or not owned by you",
        )
    if (
        notification.user_id == owner_id
        and notification.category == NotificationCategory.EMBEDDING_REINDEX
        and notification.status == NotificationStatus.RUNNING
    ):
        await mark_cancelled(notification_id)
        # Don't dismiss the notification yet — the reindex job will flip it
        # to CANCELLED with partial-state metadata, and the COMPLETED_TTL
        # will let the user see the final state in the UI before it disappears.
        return

    if notification.user_id != owner_id:
        access_checks.note_power("platform.readwrite", None, subject=f"notification:{notification_id}")
    dismissed = await service.dismiss_notification(
        notification_id=notification_id,
        user_id=owner_id or "",
        is_admin=(
            notification.user_id != owner_id
            and can_read_admin_notifications(caller, write=True)
        ),
    )

    if not dismissed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or not owned by you",
        )


# =============================================================================
# Lock Endpoints
# =============================================================================


@router.get("/locks/upload", response_model=UploadLockInfo)
async def get_upload_lock_status(
    user: CurrentActiveUser,
    ctx: Context,
) -> UploadLockInfo:
    """
    Check the current upload lock status (admin only).

    Used by admins to monitor file uploads and manage locks.

    Returns:
        Upload lock information
    """
    require_permission(await load_caller(ctx.db, user), "platform.read", None)
    lock_service = get_lock_service()
    lock_info = await lock_service.get_lock_info(UPLOAD_LOCK_NAME)

    if lock_info is None:
        return UploadLockInfo(locked=False)

    return UploadLockInfo(
        locked=True,
        owner_user_id=lock_info.owner_user_id,
        owner_email=lock_info.owner_email,
        operation=lock_info.operation,
        locked_at=lock_info.locked_at,
        expires_at=lock_info.expires_at,
    )


@router.delete("/locks/upload", status_code=status.HTTP_204_NO_CONTENT)
async def force_release_upload_lock(
    user: CurrentActiveUser,
    ctx: Context,
) -> None:
    """
    Force release the upload lock (admin only).

    Use this only for stuck locks that didn't release properly.

    Raises:
        404 if no lock exists
    """
    require_permission(await load_caller(ctx.db, user), "platform.readwrite", None)
    lock_service = get_lock_service()
    released = await lock_service.force_release_lock(UPLOAD_LOCK_NAME)

    if not released:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No upload lock exists",
        )

    logger.warning(f"Upload lock force released by admin: {user.email}")
