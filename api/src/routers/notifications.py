"""
Notifications Router

Provides REST endpoints for managing notifications and checking upload locks.
Real-time updates are delivered via WebSocket (notification:{user_id} channel).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.locks import (
    UPLOAD_LOCK_NAME,
    get_lock_service,
)
from src.models.contracts.notifications import (
    NotificationListResponse,
    NotificationPublic,
    UploadLockInfo,
)
from src.services.audit import emit_audit
from src.services.authorization import CurrentAuthorizationContext
from src.services.notification_service import get_notification_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


def _require_upload_lock_access(
    authorization: CurrentAuthorizationContext,
    capability: str,
) -> None:
    authorization.require(capability)
    authorization.require_resource_boundary(None)


def _require_admin_notification_access(
    authorization: CurrentAuthorizationContext,
) -> None:
    authorization.require("platformjobs.read")
    authorization.require_resource_boundary(None)


def _has_admin_notification_access(
    authorization: CurrentAuthorizationContext,
) -> bool:
    try:
        _require_admin_notification_access(authorization)
    except HTTPException:
        return False
    return True


# =============================================================================
# Notification Endpoints
# =============================================================================


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    authorization: CurrentAuthorizationContext,
) -> NotificationListResponse:
    """
    Get all notifications for the current user.

    Platform admins also receive admin-scoped notifications.

    Returns:
        List of active notifications
    """
    service = get_notification_service()
    actor = authorization.effective_actor
    notifications = await service.get_user_notifications(
        user_id=str(actor.user_id),
        include_admin=_has_admin_notification_access(authorization),
    )
    return NotificationListResponse(notifications=notifications)


@router.get("/{notification_id}", response_model=NotificationPublic)
async def get_notification(
    notification_id: str,
    authorization: CurrentAuthorizationContext,
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

    actor = authorization.effective_actor

    # Verify ownership. Admin authority only opens admin-scoped notifications,
    # never arbitrary private notifications owned by another user.
    if notification.user_id != str(actor.user_id):
        if not await service._is_admin_notification(notification_id):  # noqa: SLF001
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found",
            )
        _require_admin_notification_access(authorization)

    return notification


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_notification(
    notification_id: str,
    authorization: CurrentAuthorizationContext,
) -> None:
    """
    Dismiss (delete) a notification.

    Only the owner can dismiss their notification.

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
    actor = authorization.effective_actor

    # Check if this is a running embedding-reindex BEFORE dismissing —
    # we need the category and status to decide whether to set the cancel flag.
    notification = await service.get_notification(notification_id)
    if (
        notification is not None
        and notification.user_id == str(actor.user_id)
        and notification.category == NotificationCategory.EMBEDDING_REINDEX
        and notification.status == NotificationStatus.RUNNING
    ):
        await mark_cancelled(notification_id)
        # Don't dismiss the notification yet — the reindex job will flip it
        # to CANCELLED with partial-state metadata, and the COMPLETED_TTL
        # will let the user see the final state in the UI before it disappears.
        return

    if notification is not None and notification.user_id != str(actor.user_id):
        if not await service._is_admin_notification(notification_id):  # noqa: SLF001
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found or not owned by you",
            )
        _require_admin_notification_access(authorization)

    dismissed = await service.dismiss_notification(
        notification_id=notification_id,
        user_id=str(actor.user_id),
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
    authorization: CurrentAuthorizationContext,
) -> UploadLockInfo:
    """
    Check the current upload lock status (admin only).

    Used by admins to monitor file uploads and manage locks.

    Returns:
        Upload lock information
    """
    _require_upload_lock_access(authorization, "managedfiles.read")
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
    authorization: CurrentAuthorizationContext,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Force release the upload lock (admin only).

    Use this only for stuck locks that didn't release properly.

    Raises:
        404 if no lock exists
    """
    _require_upload_lock_access(authorization, "managedfiles.readwrite")
    lock_service = get_lock_service()
    released = await lock_service.force_release_lock(UPLOAD_LOCK_NAME)

    if not released:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No upload lock exists",
        )

    logger.warning(
        "Upload lock force released by actor: %s",
        authorization.effective_actor.email,
    )
    await emit_audit(
        db,
        "upload_lock.force_release",
        resource_type="upload_lock",
        details={
            "lock_name": UPLOAD_LOCK_NAME,
            "released_by": authorization.effective_actor.email,
        },
    )
    await db.commit()
