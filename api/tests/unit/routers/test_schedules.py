from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.models import CronValidationRequest
from src.routers.schedules import validate_cron_expression


@pytest.mark.asyncio
async def test_validate_cron_preview_uses_requested_timezone():
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 1, 1, 13, 30, 0, tzinfo=timezone.utc)
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    with patch("src.routers.schedules.datetime", FrozenDateTime):
        result = await validate_cron_expression(
            CronValidationRequest(
                expression="0 9 * * *",
                timezone="America/New_York",
            ),
            ctx=object(),
            user=object(),
        )

    assert result.valid is True
    assert result.next_runs is not None
    assert result.next_runs[0] == "2026-01-01T14:00:00+00:00"


@pytest.mark.asyncio
async def test_validate_cron_rejects_unknown_timezone():
    result = await validate_cron_expression(
        CronValidationRequest(
            expression="0 9 * * *",
            timezone="Not/AZone",
        ),
        ctx=object(),
        user=object(),
    )

    assert result.valid is False
    assert result.human_readable == "Invalid timezone"
    assert result.error == "Unknown timezone: Not/AZone"
