"""Persistence helpers for scheduler capacity, runs, and curated logs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from src.core.database import get_db_context
from src.models.orm.scheduler_diagnostics import (
    SchedulerReplica,
    SchedulerTaskRun,
    SchedulerTaskState,
    SystemDiagnosticLog,
)
from src.scheduler.registry import ScheduledTaskDefinition
from src.services.execution.memory_monitor import get_cgroup_memory

logger = logging.getLogger(__name__)
DIAGNOSTIC_RETENTION = timedelta(days=7)
REPLICA_ONLINE_WINDOW = timedelta(seconds=30)
REPLICA_STALE_RETENTION = REPLICA_ONLINE_WINDOW * 10


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def heartbeat_scheduler_replica(
    *, replica_id: str, hostname: str, pid: int, job_slots: int, started_at: datetime
) -> None:
    current, limit = get_cgroup_memory()
    heartbeat_at = utcnow()
    values = {
        "id": replica_id,
        "hostname": hostname,
        "pid": pid,
        "job_slots": job_slots,
        "started_at": started_at,
        "last_heartbeat_at": heartbeat_at,
        "memory_current_bytes": current if current >= 0 else None,
        "memory_limit_bytes": limit if limit > 0 else None,
    }
    async with get_db_context() as db:
        await db.execute(
            insert(SchedulerReplica)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[SchedulerReplica.id],
                set_={key: value for key, value in values.items() if key != "id"},
            )
        )
        await db.execute(
            delete(SchedulerReplica).where(
                SchedulerReplica.last_heartbeat_at
                < heartbeat_at - REPLICA_STALE_RETENTION
            )
        )


async def remove_scheduler_replica(replica_id: str) -> None:
    async with get_db_context() as db:
        await db.execute(delete(SchedulerReplica).where(SchedulerReplica.id == replica_id))


async def publish_task_states(
    tasks: list[tuple[ScheduledTaskDefinition, datetime | None]],
) -> None:
    async with get_db_context() as db:
        for definition, next_run_at in tasks:
            await db.execute(
                insert(SchedulerTaskState)
                .values(
                    task_id=definition.task_id,
                    name=definition.name,
                    schedule=definition.schedule,
                    execution_mode=definition.execution_mode,
                    enabled=True,
                    next_run_at=next_run_at,
                    updated_at=utcnow(),
                )
                .on_conflict_do_update(
                    index_elements=[SchedulerTaskState.task_id],
                    set_={
                        "name": definition.name,
                        "schedule": definition.schedule,
                        "execution_mode": definition.execution_mode,
                        "enabled": True,
                        "next_run_at": next_run_at,
                        "updated_at": utcnow(),
                    },
                )
            )


async def start_scheduler_run(task_id: str, leader_owner_id: str) -> UUID:
    async with get_db_context() as db:
        run = SchedulerTaskRun(
            task_id=task_id,
            leader_owner_id=leader_owner_id,
            status="running",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.execute(
            insert(SchedulerTaskState)
            .values(
                task_id=task_id,
                name=task_id,
                schedule="Unknown",
                execution_mode="leader",
                enabled=True,
                last_run_id=run_id,
                updated_at=utcnow(),
            )
            .on_conflict_do_update(
                index_elements=[SchedulerTaskState.task_id],
                set_={"last_run_id": run_id, "updated_at": utcnow()},
            )
        )
        await _add_log(
            db,
            source="scheduler",
            level="info",
            code="scheduled_task_started",
            message=f"{task_id} started",
            scheduler_run_id=run_id,
        )
        return run_id


async def finish_scheduler_run(
    run_id: UUID,
    *,
    status: str,
    summary: str | None = None,
    error_message: str | None = None,
    platform_job_id: UUID | None = None,
) -> None:
    completed_at = utcnow()
    async with get_db_context() as db:
        run = await db.get(SchedulerTaskRun, run_id, with_for_update=True)
        if run is None or run.status != "running":
            return
        run.status = status
        run.completed_at = completed_at
        run.duration_ms = max(0, int((completed_at - run.started_at).total_seconds() * 1000))
        run.summary = summary[:500] if summary else None
        run.error_message = error_message[:4000] if error_message else None
        run.platform_job_id = platform_job_id
        await _add_log(
            db,
            source="scheduler",
            level="error" if status == "failed" else "info",
            code="scheduled_task_failed" if status == "failed" else "scheduled_task_completed",
            message=(error_message or summary or f"{run.task_id} {status}")[:2000],
            scheduler_run_id=run.id,
            platform_job_id=platform_job_id,
        )


async def _add_log(
    db,
    *,
    source: str,
    level: str,
    code: str,
    message: str,
    scheduler_run_id: UUID | None = None,
    platform_job_id: UUID | None = None,
) -> None:
    db.add(
        SystemDiagnosticLog(
            source=source,
            level=level,
            code=code,
            message=message[:2000],
            scheduler_run_id=scheduler_run_id,
            platform_job_id=platform_job_id,
        )
    )


async def publish_system_diagnostic_log(
    *,
    source: str,
    level: str,
    code: str,
    message: str,
    scheduler_run_id: UUID | None = None,
    platform_job_id: UUID | None = None,
) -> None:
    if level not in {"debug", "info", "warning", "error"}:
        raise ValueError(f"unsupported diagnostic log level: {level}")
    async with get_db_context() as db:
        await _add_log(
            db,
            source=source,
            level=level,
            code=code,
            message=message,
            scheduler_run_id=scheduler_run_id,
            platform_job_id=platform_job_id,
        )


async def cleanup_scheduler_diagnostics() -> int:
    cutoff = utcnow() - DIAGNOSTIC_RETENTION
    async with get_db_context() as db:
        logs = await db.execute(
            delete(SystemDiagnosticLog).where(SystemDiagnosticLog.created_at < cutoff)
        )
        runs = await db.execute(
            delete(SchedulerTaskRun).where(SchedulerTaskRun.started_at < cutoff)
        )
        await db.execute(
            delete(SchedulerReplica).where(
                SchedulerReplica.last_heartbeat_at < utcnow() - REPLICA_STALE_RETENTION
            )
        )
        return (logs.rowcount or 0) + (runs.rowcount or 0)
