"""Authorization boundaries for scheduler diagnostics and notification locks."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.notifications import (
    NotificationCategory,
    NotificationPublic,
    NotificationStatus,
)
from src.routers import notifications, scheduler_diagnostics
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "ops@example.com",
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        name="Ops User",
        organization_id=None,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary or AuthorizationBoundary.platform(),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def _notification(
    *,
    user_id: str,
    notification_id: str = "notification-1",
) -> NotificationPublic:
    now = datetime.now(UTC)
    return NotificationPublic(
        id=notification_id,
        category=NotificationCategory.SYSTEM,
        title="Platform notice",
        description=None,
        status=NotificationStatus.PENDING,
        percent=None,
        error=None,
        result=None,
        metadata=None,
        created_at=now,
        updated_at=now,
        user_id=user_id,
    )


def test_scheduler_diagnostics_requires_platformjobs_read_platform() -> None:
    authorization = _authorization(
        capabilities={"platformjobs.read"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        scheduler_diagnostics._require_scheduler_diagnostics(authorization)

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


def test_upload_lock_read_requires_managedfiles_read() -> None:
    authorization = _authorization(capabilities=set())

    with pytest.raises(HTTPException) as exc:
        notifications._require_upload_lock_access(
            authorization,
            "managedfiles.read",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: managedfiles.read"


def test_admin_notification_access_requires_platformjobs_read_platform() -> None:
    authorization = _authorization(
        capabilities={"platformjobs.read"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        notifications._require_admin_notification_access(authorization)

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


@pytest.mark.asyncio
async def test_list_notifications_includes_admin_only_with_platform_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class _NotificationService:
        async def get_user_notifications(
            self,
            *,
            user_id: str,
            include_admin: bool,
        ) -> list[NotificationPublic]:
            calls.append({"user_id": user_id, "include_admin": include_admin})
            return []

    monkeypatch.setattr(
        notifications,
        "get_notification_service",
        lambda: _NotificationService(),
    )

    allowed = _authorization(capabilities={"platformjobs.read"})
    denied = _authorization(
        capabilities={"platformjobs.read"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    await notifications.list_notifications(allowed)
    await notifications.list_notifications(denied)

    assert calls == [
        {"user_id": str(allowed.effective_actor.user_id), "include_admin": True},
        {"user_id": str(denied.effective_actor.user_id), "include_admin": False},
    ]


@pytest.mark.asyncio
async def test_get_admin_notification_requires_platform_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = str(uuid4())

    class _NotificationService:
        async def get_notification(self, notification_id: str) -> NotificationPublic:
            return _notification(user_id=owner_id, notification_id=notification_id)

        async def _is_admin_notification(self, notification_id: str) -> bool:
            return True

    monkeypatch.setattr(
        notifications,
        "get_notification_service",
        lambda: _NotificationService(),
    )

    with pytest.raises(HTTPException) as exc:
        await notifications.get_notification(
            "notification-1",
            _authorization(capabilities=set()),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: platformjobs.read"


@pytest.mark.asyncio
async def test_dismiss_foreign_admin_notification_requires_platform_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = str(uuid4())
    dismissed: list[dict[str, str]] = []

    class _NotificationService:
        async def get_notification(self, notification_id: str) -> NotificationPublic:
            return _notification(user_id=owner_id, notification_id=notification_id)

        async def _is_admin_notification(self, notification_id: str) -> bool:
            return True

        async def dismiss_notification(
            self,
            *,
            notification_id: str,
            user_id: str,
        ) -> bool:
            dismissed.append(
                {"notification_id": notification_id, "user_id": user_id}
            )
            return True

    monkeypatch.setattr(
        notifications,
        "get_notification_service",
        lambda: _NotificationService(),
    )

    authorization = _authorization(capabilities={"platformjobs.read"})
    await notifications.dismiss_notification("notification-1", authorization)

    assert dismissed == [
        {
            "notification_id": "notification-1",
            "user_id": str(authorization.effective_actor.user_id),
        }
    ]


def test_upload_lock_mutation_requires_platform_boundary() -> None:
    authorization = _authorization(
        capabilities={"managedfiles.readwrite"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        notifications._require_upload_lock_access(
            authorization,
            "managedfiles.readwrite",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


@pytest.mark.asyncio
async def test_force_release_upload_lock_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_events: list[tuple[str, dict[str, object]]] = []
    commits = 0

    class _Db:
        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    class _LockService:
        async def force_release_lock(self, lock_name: str) -> bool:
            assert lock_name == notifications.UPLOAD_LOCK_NAME
            return True

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_events.append((action, kwargs))

    monkeypatch.setattr(
        notifications,
        "get_lock_service",
        lambda: _LockService(),
    )
    monkeypatch.setattr(notifications, "emit_audit", _emit_audit)

    await notifications.force_release_upload_lock(
        _authorization(
            capabilities={"managedfiles.readwrite"},
            email="operator@example.com",
        ),
        _Db(),
    )

    assert commits == 1
    assert audit_events == [
        (
            "upload_lock.force_release",
            {
                "resource_type": "upload_lock",
                "details": {
                    "lock_name": notifications.UPLOAD_LOCK_NAME,
                    "released_by": "operator@example.com",
                },
            },
        )
    ]
