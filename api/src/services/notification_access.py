"""Current-caller authorization for personal and platform notifications."""

from __future__ import annotations

from src.models.contracts.notifications import NotificationPublic
from src.services.authorization.enforce import Caller, allows_permission
from src.services.notification_service import NotificationService


def notification_owner_id(caller: Caller) -> str | None:
    """The original user whose personal notifications the caller may read."""
    return None if caller.ctx is None else str(caller.ctx.user_id)


def can_read_admin_notifications(caller: Caller, *, write: bool = False) -> bool:
    """Platform notification access is always decided at Global."""
    return allows_permission(
        caller,
        "platform.readwrite" if write else "platform.read",
        None,
    )


async def can_access_notification(
    service: NotificationService,
    caller: Caller,
    notification_id: str,
    notification: NotificationPublic | dict,
    *,
    write: bool = False,
) -> bool:
    """Authorize one notification without exposing another personal notice.

    A caller owns only notifications addressed to their original user. Named
    platform power is useful solely for notifications explicitly placed in the
    admin set; it never opens another user's personal notification by ID.
    """
    owner_id = notification_owner_id(caller)
    if owner_id is None:
        return False
    owner = (
        notification.user_id
        if isinstance(notification, NotificationPublic)
        else notification.get("user_id")
    )
    is_admin = owner != owner_id and can_read_admin_notifications(caller, write=write)
    return await service.can_access_notification(
        notification_id=notification_id,
        notification=notification,
        user_id=owner_id,
        is_admin=is_admin,
    )
