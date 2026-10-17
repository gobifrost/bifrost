from datetime import datetime, timezone
from importlib.util import find_spec

import pytest

from shared.execution_timeseries import (
    ExecutionTimeSeriesData,
    build_execution_bucket_starts,
)


def test_hourly_window_has_24_oldest_first_buckets() -> None:
    now = datetime(2026, 7, 23, 17, 45, tzinfo=timezone.utc)

    starts = build_execution_bucket_starts("24h", "UTC", now)

    assert len(starts) == 24
    assert starts[0] == datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc)
    assert starts[-1] == datetime(2026, 7, 23, 17, 0, tzinfo=timezone.utc)


def test_runtime_includes_iana_timezone_database() -> None:
    assert find_spec("tzdata") is not None

    starts = build_execution_bucket_starts(
        "7d",
        "America/Indianapolis",
        datetime(2026, 7, 23, 17, 45, tzinfo=timezone.utc),
    )

    assert len(starts) == 7


def test_daily_window_uses_local_midnights_across_dst() -> None:
    now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)

    starts = build_execution_bucket_starts("7d", "America/New_York", now)

    assert len(starts) == 7
    assert starts[-2] == datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)
    assert starts[-1] == datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid timezone"):
        build_execution_bucket_starts(
            "7d",
            "Not/A_Timezone",
            datetime(2026, 7, 23, tzinfo=timezone.utc),
        )


def test_empty_series_has_no_success_rate() -> None:
    series = ExecutionTimeSeriesData(
        window="30d",
        timezone="UTC",
        buckets=(),
        success_count=0,
        failed_count=0,
    )

    assert series.total_count == 0
    assert series.success_rate is None


@pytest.mark.asyncio
@pytest.mark.parametrize("read_all,expected", [(False, 1), (True, 2)])
async def test_chart_uses_original_execution_visibility_before_aggregation(db_session, monkeypatch, read_all, expected):
    from uuid import uuid4
    from shared.builtin_roles import USER_ROLE_ID
    from shared.execution_timeseries import get_execution_time_series
    from src.core.principal import UserPrincipal
    from src.models.enums import ExecutionStatus
    from src.models.orm.executions import Execution
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User
    from src.services.authorization.context import AuthorizationContext
    from src.services.authorization.enforce import Caller

    home, foreign = Organization(name="Contoso", created_by="test"), Organization(name="Fabrikam", created_by="test")
    db_session.add_all([home, foreign])
    await db_session.flush()
    original = User(email=f"chart-{uuid4().hex}@contoso.example", organization_id=home.id)
    other = User(email=f"chart-{uuid4().hex}@fabrikam.example", organization_id=foreign.id)
    db_session.add_all([original, other])
    await db_session.flush()
    principal = UserPrincipal(user_id=original.id, email=original.email, organization_id=home.id, is_superuser=True, is_provider_org=True)
    permissions = {"metrics.read"}
    if read_all:
        permissions.add("executions.read.all")
    caller = Caller(principal, AuthorizationContext(
        user_id=original.id, home_organization_id=home.id, base_role_id=USER_ROLE_ID,
        is_external=False, base_permissions=frozenset(permissions),
    ))

    async def current_caller(_db, _principal):
        return caller

    monkeypatch.setattr("shared.execution_timeseries.load_caller", current_caller)
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    for organization_id, owner in [(home.id, original.id), (home.id, other.id), (foreign.id, original.id), (foreign.id, other.id)]:
        db_session.add(Execution(
            workflow_name="Contoso Chart", organization_id=organization_id,
            executed_by=owner, executed_by_name="Contoso User", status=ExecutionStatus.SUCCESS,
            started_at=now,
        ))
    await db_session.flush()

    series = await get_execution_time_series(db_session, principal, "24h", "UTC", now=now)
    assert series.success_count == expected
    assert series.failed_count == 0
