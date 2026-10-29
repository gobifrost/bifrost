from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.contracts.notifications import NotificationPublic, NotificationStatus
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.notification_access import can_access_notification


def _caller(user_id):
    principal = UserPrincipal(user_id=user_id, email="person@example.com", organization_id=None)
    return Caller(principal, AuthorizationContext(
        user_id=user_id, home_organization_id=None, base_role_id=uuid4(),
        base_permissions=frozenset(), is_external=False,
    ))


def _notice(owner_id):
    return NotificationPublic(
        id=str(uuid4()), category="system", title="Notice", description="", status=NotificationStatus.PENDING,
        percent=None, error=None, result=None, metadata={},
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        user_id=str(owner_id),
    )


@pytest.mark.asyncio
async def test_other_personal_notification_stays_hidden_even_when_platform_power_is_claimed(monkeypatch):
    caller = _caller(uuid4())
    notice = _notice(uuid4())
    service = AsyncMock()
    service.can_access_notification.return_value = False
    monkeypatch.setattr(
        "src.services.notification_access.can_read_admin_notifications", lambda *_args, **_kwargs: True
    )

    assert not await can_access_notification(service, caller, notice.id, notice)
    service.can_access_notification.assert_awaited_once_with(
        notification_id=notice.id,
        notification=notice,
        user_id=str(caller.ctx.user_id),
        is_admin=True,
    )
