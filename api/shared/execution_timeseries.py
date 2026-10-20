"""Execution time-series aggregation for the dashboard."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.scope_resolver import UNSET, resolve_effective_scope
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.services.authorization.enforce import load_caller, require_permission
from src.services.authorization.execution_access import execution_list_filter

ExecutionChartWindow = Literal["24h", "7d", "30d"]

_WINDOW_BUCKET_COUNTS: dict[ExecutionChartWindow, int] = {
    "24h": 24,
    "7d": 7,
    "30d": 30,
}
_FAILURE_STATUSES = (
    ExecutionStatus.FAILED,
    ExecutionStatus.TIMEOUT,
    ExecutionStatus.STUCK,
    ExecutionStatus.COMPLETED_WITH_ERRORS,
)
_TERMINAL_STATUSES = (ExecutionStatus.SUCCESS, *_FAILURE_STATUSES)


@dataclass(frozen=True)
class ExecutionTimeSeriesBucketData:
    start: datetime
    success_count: int
    failed_count: int


@dataclass(frozen=True)
class ExecutionTimeSeriesData:
    window: ExecutionChartWindow
    timezone: str
    buckets: tuple[ExecutionTimeSeriesBucketData, ...]
    success_count: int
    failed_count: int

    @property
    def total_count(self) -> int:
        return self.success_count + self.failed_count

    @property
    def success_rate(self) -> float | None:
        if self.total_count == 0:
            return None
        return self.success_count / self.total_count * 100


def _validated_zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Invalid timezone: {timezone_name}") from exc


def build_execution_bucket_starts(
    window: ExecutionChartWindow,
    timezone_name: str,
    now: datetime | None = None,
) -> tuple[datetime, ...]:
    """Return oldest-first UTC starts for the requested local chart buckets."""

    zone = _validated_zone(timezone_name)
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    bucket_count = _WINDOW_BUCKET_COUNTS[window]
    local_now = now_utc.astimezone(zone)

    if window == "24h":
        newest_start = local_now.replace(
            minute=0,
            second=0,
            microsecond=0,
        ).astimezone(timezone.utc)
        return tuple(
            newest_start - timedelta(hours=offset)
            for offset in range(bucket_count - 1, -1, -1)
        )

    newest_date = local_now.date()
    return tuple(
        datetime.combine(
            newest_date - timedelta(days=offset),
            time.min,
            tzinfo=zone,
        ).astimezone(timezone.utc)
        for offset in range(bucket_count - 1, -1, -1)
    )


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_execution_time_series(
    db: AsyncSession,
    user: UserPrincipal,
    window: ExecutionChartWindow,
    timezone_name: str,
    scope: str | None = None,
    now: datetime | None = None,
) -> ExecutionTimeSeriesData:
    """Aggregate terminal execution outcomes without loading individual rows."""

    caller = await load_caller(db, user)
    requested = UNSET if not scope else None if scope == "global" else UUID(scope)
    permission_org = resolve_effective_scope(caller=caller, requested_scope=requested)
    require_permission(caller, "metrics.read", permission_org)

    now_utc = _normalize_utc(now or datetime.now(timezone.utc))
    bucket_starts = build_execution_bucket_starts(
        window,
        timezone_name,
        now_utc,
    )
    unit = "hour" if window == "24h" else "day"

    local_started_at = func.timezone(timezone_name, Execution.started_at)
    local_bucket_start = func.date_trunc(unit, local_started_at)
    bucket_start = func.timezone(timezone_name, local_bucket_start)

    conditions = [
        Execution.started_at >= bucket_starts[0],
        Execution.started_at <= now_utc,
        Execution.status.in_(_TERMINAL_STATUSES),
        execution_list_filter(caller, Execution),
    ]

    if scope:
        conditions.append(
            Execution.organization_id.is_(None)
            if scope == "global" else Execution.organization_id == permission_org
        )

    query = (
        select(
            bucket_start.label("bucket_start"),
            func.sum(
                case((Execution.status == ExecutionStatus.SUCCESS, 1), else_=0)
            ).label("success_count"),
            func.sum(
                case((Execution.status.in_(_FAILURE_STATUSES), 1), else_=0)
            ).label("failed_count"),
        )
        .where(*conditions)
        .group_by(bucket_start)
        .order_by(bucket_start)
    )
    rows = (await db.execute(query)).all()
    counts_by_start = {
        _normalize_utc(row.bucket_start): (
            int(row.success_count or 0),
            int(row.failed_count or 0),
        )
        for row in rows
    }

    buckets = tuple(
        ExecutionTimeSeriesBucketData(
            start=start,
            success_count=counts_by_start.get(start, (0, 0))[0],
            failed_count=counts_by_start.get(start, (0, 0))[1],
        )
        for start in bucket_starts
    )
    return ExecutionTimeSeriesData(
        window=window,
        timezone=timezone_name,
        buckets=buckets,
        success_count=sum(bucket.success_count for bucket in buckets),
        failed_count=sum(bucket.failed_count for bucket in buckets),
    )
