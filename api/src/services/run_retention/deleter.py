"""Delete finished runs and events past the retention window.

The rule this module enforces: every delete runs in a transaction that holds
the job's current lease and removes exactly the ids it selected. Workflow runs
are rolled up into ``workflow_run_daily`` and their AI usage is stamped with
the workflow, agent runs' usage with the agent, in that same transaction, so
history and cost attribution outlive the runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.core.database import get_db_context
from src.models.contracts.run_retention import RunRetentionSettings
from src.models.enums import ExecutionStatus
from src.models.orm import AgentRun, AIUsage, Event, Execution, PlatformJob, WorkflowRunDaily
from src.services.platform_job_lease import hold_lease

RUN_RETENTION_JOB_TYPE = "run.retention"

FINISHED_WORKFLOW_STATUSES: tuple[ExecutionStatus, ...] = (
    ExecutionStatus.SUCCESS,
    ExecutionStatus.FAILED,
    ExecutionStatus.TIMEOUT,
    ExecutionStatus.CANCELLED,
    ExecutionStatus.COMPLETED_WITH_ERRORS,
    ExecutionStatus.STUCK,
)
FINISHED_AGENT_STATUSES: tuple[str, ...] = (
    "completed",
    "failed",
    "budget_exceeded",
    "timeout",
    "cancelled",
)

ROLLUP_SQL = text("""
INSERT INTO workflow_run_daily
       (day, organization_id, workflow_id, workflow_name, status, run_count, total_duration_ms,
        total_cpu_seconds, max_peak_cpu_cores, max_peak_process_rss_bytes, max_peak_memory_bytes)
SELECT (timezone('UTC', coalesce(started_at, completed_at)))::date, organization_id, workflow_id,
       workflow_name, status, count(*), coalesce(sum(duration_ms), 0), coalesce(sum(cpu_total_seconds), 0),
       max(peak_cpu_cores), max(peak_process_rss_bytes), max(peak_memory_bytes)
  FROM executions WHERE id = ANY(:ids)
 GROUP BY 1, 2, 3, 4, 5
ON CONFLICT (day, organization_id, workflow_id, workflow_name, status) DO UPDATE SET
       run_count = workflow_run_daily.run_count + EXCLUDED.run_count,
       total_duration_ms = workflow_run_daily.total_duration_ms + EXCLUDED.total_duration_ms,
       total_cpu_seconds = workflow_run_daily.total_cpu_seconds + EXCLUDED.total_cpu_seconds,
       max_peak_cpu_cores = GREATEST(workflow_run_daily.max_peak_cpu_cores, EXCLUDED.max_peak_cpu_cores),
       max_peak_process_rss_bytes = GREATEST(workflow_run_daily.max_peak_process_rss_bytes, EXCLUDED.max_peak_process_rss_bytes),
       max_peak_memory_bytes = GREATEST(workflow_run_daily.max_peak_memory_bytes, EXCLUDED.max_peak_memory_bytes),
       updated_at = NOW()
""")


class DeleteMismatch(Exception):
    """The delete removed a different number of rows than were selected."""


def _expired_workflow_runs(cutoff: datetime):
    return (Execution.completed_at < cutoff, Execution.status.in_(FINISHED_WORKFLOW_STATUSES))


def _expired_agent_runs(cutoff: datetime):
    return (AgentRun.completed_at < cutoff, AgentRun.status.in_(FINISHED_AGENT_STATUSES))


async def delete_workflow_run_batch(
    *, cutoff: datetime, job_id: UUID, lease_token: UUID, limit: int = 1000
) -> int:
    """Roll up, stamp and delete the oldest finished workflow runs before ``cutoff``, under the lease."""
    async with get_db_context() as db:
        await hold_lease(db, job_id, lease_token)
        ids = list(
            (
                await db.execute(
                    select(Execution.id)
                    .where(*_expired_workflow_runs(cutoff))
                    .order_by(Execution.completed_at, Execution.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        if not ids:
            return 0
        await db.execute(ROLLUP_SQL, {"ids": ids})
        await db.execute(
            update(AIUsage)
            .where(AIUsage.execution_id.in_(ids))
            .values(
                workflow_id=select(Execution.workflow_id)
                .where(Execution.id == AIUsage.execution_id)
                .scalar_subquery()
            )
        )
        deleted = await db.execute(delete(Execution).where(Execution.id.in_(ids)))
        if deleted.rowcount != len(ids):
            raise DeleteMismatch(f"workflow runs: deleted {deleted.rowcount} of {len(ids)}")
        await db.commit()
        return len(ids)


async def delete_agent_run_batch(
    *, cutoff: datetime, job_id: UUID, lease_token: UUID, limit: int = 500
) -> int:
    """Stamp and delete the oldest finished leaf agent runs before ``cutoff``, under the lease.

    A parent becomes a leaf once its children are deleted; selecting leaves only
    means the ``parent_run_id`` cascade never removes a child. Steps, verdict
    history and flag conversations cascade in the database.
    """
    child = aliased(AgentRun)
    async with get_db_context() as db:
        await hold_lease(db, job_id, lease_token)
        ids = list(
            (
                await db.execute(
                    select(AgentRun.id)
                    .where(
                        *_expired_agent_runs(cutoff),
                        ~exists(select(1).where(child.parent_run_id == AgentRun.id)),
                    )
                    .order_by(AgentRun.completed_at, AgentRun.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        if not ids:
            return 0
        await db.execute(
            update(AIUsage)
            .where(AIUsage.agent_run_id.in_(ids))
            .values(
                agent_id=select(AgentRun.agent_id)
                .where(AgentRun.id == AIUsage.agent_run_id)
                .scalar_subquery()
            )
        )
        deleted = await db.execute(delete(AgentRun).where(AgentRun.id.in_(ids)))
        if deleted.rowcount != len(ids):
            raise DeleteMismatch(f"agent runs: deleted {deleted.rowcount} of {len(ids)}")
        await db.commit()
        return len(ids)


async def delete_event_batch(
    *, cutoff: datetime, job_id: UUID, lease_token: UUID, limit: int = 1000
) -> int:
    """Delete the oldest events created before ``cutoff``, under the lease; deliveries cascade."""
    async with get_db_context() as db:
        await hold_lease(db, job_id, lease_token)
        ids = list(
            (
                await db.execute(
                    select(Event.id)
                    .where(Event.created_at < cutoff)
                    .order_by(Event.created_at, Event.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        if not ids:
            return 0
        deleted = await db.execute(delete(Event).where(Event.id.in_(ids)))
        if deleted.rowcount != len(ids):
            raise DeleteMismatch(f"events: deleted {deleted.rowcount} of {len(ids)}")
        await db.commit()
        return len(ids)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def plan_run_retention(db: AsyncSession, *, cutoff: datetime) -> dict[str, Any]:
    """What a run would delete at ``cutoff``, from SQL aggregates only. JSON-ready.

    Agent runs count whether or not they are leaves yet: a run deletes children
    before parents, so every finished old run goes eventually.
    """
    workflow_runs, oldest_workflow_run = (
        await db.execute(
            select(func.count(), func.min(Execution.completed_at)).where(*_expired_workflow_runs(cutoff))
        )
    ).one()
    agent_runs, oldest_agent_run = (
        await db.execute(
            select(func.count(), func.min(AgentRun.completed_at)).where(*_expired_agent_runs(cutoff))
        )
    ).one()
    events, oldest_event = (
        await db.execute(select(func.count(), func.min(Event.created_at)).where(Event.created_at < cutoff))
    ).one()
    return {
        "cutoff": cutoff.isoformat(),
        "workflow_runs": workflow_runs,
        "agent_runs": agent_runs,
        "events": events,
        "oldest_workflow_run": _iso(oldest_workflow_run),
        "oldest_agent_run": _iso(oldest_agent_run),
        "oldest_event": _iso(oldest_event),
    }


async def run_retention_info(db: AsyncSession, settings: RunRetentionSettings) -> dict[str, Any]:
    """The configured window plus the oldest finished run and what the rollup holds."""
    oldest = await db.scalar(
        select(func.min(Execution.completed_at)).where(Execution.status.in_(FINISHED_WORKFLOW_STATUSES))
    )
    rolled_up_runs, rolled_up_through = (
        await db.execute(
            select(func.coalesce(func.sum(WorkflowRunDaily.run_count), 0), func.max(WorkflowRunDaily.day))
        )
    ).one()
    return {
        "days": settings.days,
        "oldest_finished_run": oldest,
        "rolled_up_runs": int(rolled_up_runs),
        "rolled_up_through": rolled_up_through,
    }


async def latest_run_retention_job(db: AsyncSession) -> PlatformJob | None:
    """The newest ``run.retention`` run, scheduled or manual, dry run or not."""
    return await db.scalar(
        select(PlatformJob)
        .where(PlatformJob.job_type == RUN_RETENTION_JOB_TYPE)
        .order_by(PlatformJob.created_at.desc())
        .limit(1)
    )
