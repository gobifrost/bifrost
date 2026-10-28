"""Durable handler that deletes finished runs and events past the retention window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cache.redis_client import get_redis
from src.core.database import get_db_context
from src.core.principal import UserPrincipal
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobFailure,
    define_platform_job,
)
from src.jobs.platform.system_maintenance import enqueue_system_maintenance
from src.models.orm.platform_jobs import PlatformJob
from src.scheduler.registry import ScheduledTaskOutcome
from src.services.agent_run_step_streams import inspect_orphaned_agent_run_step_streams
from src.services.platform_job_lease import LeaseLost
from src.services.platform_jobs import (
    enqueue_platform_job,
    ensure_platform_job_notification,
    publish_platform_job_update,
)
from src.services.run_retention.deleter import (
    RUN_RETENTION_JOB_TYPE,
    DeleteMismatch,
    delete_agent_run_batch,
    delete_event_batch,
    delete_workflow_run_batch,
    plan_run_retention,
    reclaim_orphaned_execution_log_streams,
)
from src.services.run_retention.settings import RunRetentionSettingsService

# Rows deleted per table in one attempt; the next day's run continues past it.
MAX_ROWS_PER_TABLE = 100_000


class RunRetentionPayload(BaseModel):
    dry_run: bool = False


async def run_run_retention(context: PlatformJobContext, payload: RunRetentionPayload) -> dict:
    # The cutoff is frozen on first start so a resumed attempt deletes exactly
    # what the original attempt would have, whatever the settings say now.
    state = dict(context.checkpoint or {})
    if "cutoff" not in state:
        async with get_db_context() as db:
            settings = await RunRetentionSettingsService(db).get_settings()
        if settings.days is None:
            result = {"dry_run": payload.dry_run, "skipped": "keep_forever"}
            if not payload.dry_run:
                result["execution_log_orphans"] = await reclaim_orphaned_execution_log_streams()
            return result
        state = {
            "dry_run": payload.dry_run,
            "cutoff": (datetime.now(timezone.utc) - timedelta(days=settings.days)).isoformat(),
            "workflow_runs_deleted": 0,
            "agent_runs_deleted": 0,
            "events_deleted": 0,
            "continues": False,
        }
    cutoff = datetime.fromisoformat(state["cutoff"])

    if payload.dry_run:
        async with get_db_context() as db:
            retention_plan = await plan_run_retention(db, cutoff=cutoff)
            async with get_redis() as redis:
                stream_plan = await inspect_orphaned_agent_run_step_streams(db, redis)
            return {
                "dry_run": True,
                **retention_plan,
                "agent_run_step_streams": stream_plan,
            }

    await context.save_checkpoint(state, phase="Deleting")
    for key, label, delete_batch in (
        ("workflow_runs_deleted", "workflow runs", delete_workflow_run_batch),
        ("agent_runs_deleted", "agent runs", delete_agent_run_batch),
        ("events_deleted", "events", delete_event_batch),
    ):
        this_run = 0
        while this_run < MAX_ROWS_PER_TABLE:
            try:
                deleted = await delete_batch(
                    cutoff=cutoff, job_id=context.job_id, lease_token=context.lease_token
                )
            except LeaseLost as exc:
                raise PlatformJobCancelled from exc
            except DeleteMismatch as exc:
                raise PlatformJobFailure(
                    "run_delete_mismatch",
                    f"{exc}. The batch was rolled back.",
                    retryable=True,
                    result=state,
                ) from exc
            if not deleted:
                break
            this_run += deleted
            state[key] += deleted
            await context.save_checkpoint(state, phase="Deleting")
            await context.report(f"Deleted {state[key]} {label}", current=state[key])
        else:
            state["continues"] = True

    state["execution_log_orphans"] = await reclaim_orphaned_execution_log_streams()

    await context.log(
        "info",
        "run_retention_completed",
        f"Deleted {state['workflow_runs_deleted']} workflow runs, "
        f"{state['agent_runs_deleted']} agent runs and {state['events_deleted']} events"
        + ("; continues tomorrow." if state["continues"] else "."),
    )
    return state


RUN_RETENTION_DEFINITION = define_platform_job(
    RUN_RETENTION_JOB_TYPE, RunRetentionPayload, run_run_retention
)


async def enqueue_automatic_run_retention() -> ScheduledTaskOutcome:
    return await enqueue_system_maintenance(
        RUN_RETENTION_DEFINITION,
        RunRetentionPayload(),
        title="Delete expired runs and events",
    )


async def enqueue_manual_run_retention(
    db: AsyncSession, user: UserPrincipal, *, dry_run: bool
) -> tuple[PlatformJob, bool]:
    """Queue an administrator-requested retention run or preview (deduplicated)."""
    job, reused = await enqueue_platform_job(
        db,
        RUN_RETENTION_DEFINITION,
        RunRetentionPayload(dry_run=dry_run),
        dedupe_key="manual-dry-run" if dry_run else "manual",
        resource_lock_key=RUN_RETENTION_DEFINITION.job_type,
        priority=500,
        organization_id=None,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="system",
        resource_id=RUN_RETENTION_DEFINITION.job_type,
        title="Preview run retention" if dry_run else "Delete expired runs and events",
        action_url="/settings/maintenance",
    )
    if job.notification_id is None:
        await ensure_platform_job_notification(db, job)
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    return job, reused
