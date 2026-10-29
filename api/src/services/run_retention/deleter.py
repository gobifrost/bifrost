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

from src.core.cache import get_redis
from src.core.cache.keys import execution_log_retention_cursor_key, execution_logs_stream_key
from src.core.database import get_db_context
from src.models.contracts.run_retention import RunRetentionSettings
from src.models.enums import ExecutionStatus
from src.models.orm import AgentRun, AIUsage, Event, Execution, PlatformJob, WorkflowRunDaily
from src.services.agent_run_step_streams import delete_agent_run_step_streams
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
WITH ai AS (
    SELECT execution_id, coalesce(sum(cost), 0) AS ai_cost, count(*) AS ai_calls
      FROM ai_usage WHERE execution_id = ANY(:ids)
     GROUP BY execution_id
)
INSERT INTO workflow_run_daily
       (day, organization_id, workflow_id, workflow_name, status, run_count, total_duration_ms,
        total_cpu_seconds, max_peak_cpu_cores, max_peak_process_rss_bytes, max_peak_memory_bytes,
        total_ai_cost, total_ai_calls)
SELECT (timezone('UTC', coalesce(e.started_at, e.completed_at)))::date, e.organization_id, e.workflow_id,
       e.workflow_name, e.status, count(*), coalesce(sum(e.duration_ms), 0), coalesce(sum(e.cpu_total_seconds), 0),
       max(e.peak_cpu_cores), max(e.peak_process_rss_bytes), max(e.peak_memory_bytes),
       coalesce(sum(ai.ai_cost), 0), coalesce(sum(ai.ai_calls), 0)
  FROM executions e LEFT JOIN ai ON ai.execution_id = e.id
 WHERE e.id = ANY(:ids)
 GROUP BY 1, 2, 3, 4, 5
ON CONFLICT (day, organization_id, workflow_id, workflow_name, status) DO UPDATE SET
       run_count = workflow_run_daily.run_count + EXCLUDED.run_count,
       total_duration_ms = workflow_run_daily.total_duration_ms + EXCLUDED.total_duration_ms,
       total_cpu_seconds = workflow_run_daily.total_cpu_seconds + EXCLUDED.total_cpu_seconds,
       max_peak_cpu_cores = GREATEST(workflow_run_daily.max_peak_cpu_cores, EXCLUDED.max_peak_cpu_cores),
       max_peak_process_rss_bytes = GREATEST(workflow_run_daily.max_peak_process_rss_bytes, EXCLUDED.max_peak_process_rss_bytes),
       max_peak_memory_bytes = GREATEST(workflow_run_daily.max_peak_memory_bytes, EXCLUDED.max_peak_memory_bytes),
       total_ai_cost = workflow_run_daily.total_ai_cost + EXCLUDED.total_ai_cost,
       total_ai_calls = workflow_run_daily.total_ai_calls + EXCLUDED.total_ai_calls,
       updated_at = NOW()
""")


class DeleteMismatch(Exception):
    """The delete removed a different number of rows than were selected."""


async def delete_execution_log_streams(execution_ids: list[UUID]) -> None:
    """Delete streams only after their execution-row transaction committed."""
    if not execution_ids:
        return
    try:
        async with get_redis() as redis:
            await redis.delete(*(execution_logs_stream_key(str(execution_id)) for execution_id in execution_ids))
    except Exception as exc:
        # A committed database deletion must not be undone for Redis cleanup.
        # The bounded orphan sweep below will retry this key after a crash.
        import logging

        logging.getLogger(__name__).warning("Failed to delete retained execution-log streams: %s", exc)


async def reclaim_orphaned_execution_log_streams(*, limit: int = 100) -> dict[str, int]:
    """Boundedly reclaim valid execution streams with no durable execution."""
    async with get_redis() as redis:
        cursor = int(await redis.get(execution_log_retention_cursor_key()) or 0)
        next_cursor, keys = await redis.scan(
            cursor=cursor, match="bifrost:logs:*", count=limit
        )
    candidates: dict[UUID, str] = {}
    for key in keys:
        try:
            execution_id = UUID(key.removeprefix("bifrost:logs:"))
        except ValueError:
            continue
        candidates[execution_id] = key
    if candidates:
        async with get_db_context() as db:
            existing = set(
                (await db.scalars(select(Execution.id).where(Execution.id.in_(candidates)))).all()
            )
        orphaned_keys = [key for execution_id, key in candidates.items() if execution_id not in existing]
        if orphaned_keys:
            async with get_redis() as redis:
                await redis.delete(*orphaned_keys)
    else:
        orphaned_keys = []
    async with get_redis() as redis:
        await redis.set(execution_log_retention_cursor_key(), next_cursor)
    return {"scanned": len(keys), "orphaned": len(orphaned_keys)}


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
        await delete_execution_log_streams(ids)
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
        await delete_agent_run_step_streams(ids)
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

    It counts every finished run past the window, agent runs whether or not
    they are leaves yet. A parent whose child is unfinished or younger stays
    until that child is deleted, so the agent run count can be higher than
    what a run deletes.
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
