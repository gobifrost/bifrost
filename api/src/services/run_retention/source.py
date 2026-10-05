"""The one history source for workflow runs.

Every finished workflow run is counted exactly once: in ``executions`` while it
is kept, and in ``workflow_run_daily`` after the retention job deletes it.
Readers aggregate this source instead of ``executions`` so their totals are the
same before and after a deletion.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, Integer, Subquery, case, cast, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm import Execution, WorkflowRunDaily


def _filters(
    model: type[Execution] | type[WorkflowRunDaily],
    organization_id: UUID | None,
    workflow_id: UUID | None,
    workflow_name_like: str | None,
    status: ExecutionStatus | None,
) -> list:
    conditions = []
    if organization_id is not None:
        conditions.append(model.organization_id == organization_id)
    if workflow_id is not None:
        conditions.append(model.workflow_id == workflow_id)
    if workflow_name_like:
        conditions.append(model.workflow_name.ilike(f"%{workflow_name_like}%"))
    if status is not None:
        conditions.append(model.status == status)
    return conditions


def workflow_run_source(
    *,
    start: datetime,
    end: datetime,
    organization_id: UUID | None = None,
    workflow_id: UUID | None = None,
    workflow_name_like: str | None = None,
    status: ExecutionStatus | None = None,
) -> Subquery:
    """Kept runs started in ``[start, end]`` plus rolled-up days in that range.

    Kept runs give one row each with ``run_count = 1``, whatever their status.
    Rolled-up rows are the ``workflow_run_daily`` days between the UTC dates of
    ``start`` and ``end``; their ``last_started_at`` is the day's UTC midnight.
    ``start`` and ``end`` must be timezone-aware.
    """
    first_day = start.astimezone(UTC).date()
    last_day = end.astimezone(UTC).date()
    filters = (organization_id, workflow_id, workflow_name_like, status)

    kept = select(
        Execution.workflow_id.label("workflow_id"),
        Execution.workflow_name.label("workflow_name"),
        Execution.organization_id.label("organization_id"),
        Execution.status.label("status"),
        literal(1, Integer).label("run_count"),
        Execution.duration_ms.label("total_duration_ms"),
        Execution.cpu_total_seconds.label("total_cpu_seconds"),
        Execution.peak_cpu_cores.label("max_peak_cpu_cores"),
        Execution.peak_process_rss_bytes.label("max_peak_process_rss_bytes"),
        Execution.peak_memory_bytes.label("max_peak_memory_bytes"),
        Execution.started_at.label("last_started_at"),
    ).where(Execution.started_at >= start, Execution.started_at <= end, *_filters(Execution, *filters))

    rolled_up = select(
        WorkflowRunDaily.workflow_id,
        WorkflowRunDaily.workflow_name,
        WorkflowRunDaily.organization_id,
        WorkflowRunDaily.status,
        WorkflowRunDaily.run_count,
        WorkflowRunDaily.total_duration_ms,
        WorkflowRunDaily.total_cpu_seconds,
        WorkflowRunDaily.max_peak_cpu_cores,
        WorkflowRunDaily.max_peak_process_rss_bytes,
        WorkflowRunDaily.max_peak_memory_bytes,
        func.timezone("UTC", cast(WorkflowRunDaily.day, DateTime()), type_=DateTime(timezone=True)).label(
            "last_started_at"
        ),
    ).where(
        WorkflowRunDaily.day >= first_day,
        WorkflowRunDaily.day <= last_day,
        *_filters(WorkflowRunDaily, *filters),
    )

    return union_all(kept, rolled_up).subquery("workflow_runs")


async def all_time_run_totals(db: AsyncSession) -> dict[str, int]:
    """All-time run counts: every kept execution plus every rolled-up run.

    ``failed`` counts the ``Failed`` status only, as the dashboard always has.
    """
    kept = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Execution.status == ExecutionStatus.SUCCESS),
                func.count().filter(Execution.status == ExecutionStatus.FAILED),
            ).select_from(Execution)
        )
    ).one()
    rolled_up = (
        await db.execute(
            select(
                func.coalesce(func.sum(WorkflowRunDaily.run_count), 0),
                func.coalesce(
                    func.sum(case((WorkflowRunDaily.status == ExecutionStatus.SUCCESS, WorkflowRunDaily.run_count))), 0
                ),
                func.coalesce(
                    func.sum(case((WorkflowRunDaily.status == ExecutionStatus.FAILED, WorkflowRunDaily.run_count))), 0
                ),
            )
        )
    ).one()
    return {
        key: int(kept_value) + int(rolled_value)
        for key, kept_value, rolled_value in zip(("total", "success", "failed"), kept, rolled_up, strict=True)
    }
