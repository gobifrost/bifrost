from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models import CronValidationRequest
from src.routers.schedules import validate_cron_expression
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


def _workflow_reader() -> Caller:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="schedule-reader@example.com",
        organization_id=None,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=None,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset({"workflows.read"}),
        ),
    )


@pytest.mark.asyncio
async def test_validate_cron_preview_uses_requested_timezone(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 1, 1, 13, 30, 0, tzinfo=timezone.utc)
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    caller = _workflow_reader()
    monkeypatch.setattr(
        "src.routers.schedules.load_caller", AsyncMock(return_value=caller)
    )
    with patch("src.routers.schedules.datetime", FrozenDateTime):
        result = await validate_cron_expression(
            CronValidationRequest(
                expression="0 9 * * *",
                timezone="America/New_York",
            ),
            ctx=object(),
            user=caller.principal,
            db=object(),
        )

    assert result.valid is True
    assert result.next_runs is not None
    assert result.next_runs[0] == "2026-01-01T14:00:00+00:00"


@pytest.mark.asyncio
async def test_validate_cron_rejects_unknown_timezone(monkeypatch):
    caller = _workflow_reader()
    monkeypatch.setattr(
        "src.routers.schedules.load_caller", AsyncMock(return_value=caller)
    )
    result = await validate_cron_expression(
        CronValidationRequest(
            expression="0 9 * * *",
            timezone="Not/AZone",
        ),
        ctx=object(),
        user=caller.principal,
        db=object(),
    )

    assert result.valid is False
    assert result.human_readable == "Invalid timezone"
    assert result.error == "Unknown timezone: Not/AZone"


@pytest.mark.asyncio
async def test_validate_cron_accepts_legacy_indianapolis_alias(monkeypatch):
    caller = _workflow_reader()
    monkeypatch.setattr(
        "src.routers.schedules.load_caller", AsyncMock(return_value=caller)
    )
    result = await validate_cron_expression(
        CronValidationRequest(
            expression="0 9 * * *",
            timezone="America/Indianapolis",
        ),
        ctx=object(),
        user=caller.principal,
        db=object(),
    )

    assert result.valid is True
    assert result.next_runs is not None


@pytest.mark.asyncio
async def test_validate_cron_uses_verified_runtime_scope_not_effect_actor_home(monkeypatch):
    """The resolver owns the original-user runtime default selection."""
    caller = MagicMock()
    selected_org = uuid4()
    resolver = MagicMock(return_value=selected_org)
    required = MagicMock()
    monkeypatch.setattr("src.routers.schedules.load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr("src.routers.schedules.resolve_effective_scope", resolver)
    monkeypatch.setattr("src.routers.schedules.require_permission", required)

    await validate_cron_expression(
        CronValidationRequest(expression="0 9 * * *", timezone="UTC"),
        ctx=object(),
        user=MagicMock(),
        db=object(),
    )

    assert resolver.call_args.kwargs["caller"] is caller
    required.assert_called_once_with(caller, "workflows.read", selected_org)
