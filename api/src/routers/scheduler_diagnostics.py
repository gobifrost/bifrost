"""Read-only scheduler diagnostics for platform administrators."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.scheduler_diagnostics import (
    SchedulerCapacityStatus,
    SchedulerDiagnosticsResponse,
    SchedulerLeaderStatus,
    SchedulerReplicaStatus,
    SchedulerTaskHistoryResponse,
    SchedulerTaskRunDetail,
    SchedulerTaskRunStatus,
    SchedulerTaskStatus,
    SystemDiagnosticLogPublic,
)
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.scheduler_diagnostics import (
    SchedulerReplica,
    SchedulerTaskRun,
    SchedulerTaskState,
    SystemDiagnosticLog,
)
from src.models.orm.scheduler_leases import SchedulerLease
from src.scheduler.leadership import TRIGGER_LEASE_NAME
from src.scheduler.registry import SCHEDULED_TASKS, SCHEDULED_TASKS_BY_ID
from src.services.scheduler_diagnostics import (
    REPLICA_ONLINE_WINDOW,
    REPLICA_STALE_RETENTION,
    utcnow,
)

router = APIRouter(prefix="/api/platform/scheduler", tags=["Platform Scheduler"])


def _run_status(
    run: SchedulerTaskRun,
    linked_jobs: dict[UUID, PlatformJob],
) -> SchedulerTaskRunStatus:
    linked_job = linked_jobs.get(run.platform_job_id)
    return SchedulerTaskRunStatus(
        id=run.id,
        status=run.status,
        leader_owner_id=run.leader_owner_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        summary=run.summary,
        error_message=run.error_message,
        platform_job_id=run.platform_job_id,
        platform_job_status=linked_job.status if linked_job else None,
        platform_job_memory_start_bytes=(
            linked_job.memory_start_bytes if linked_job else None
        ),
        platform_job_memory_peak_bytes=(
            linked_job.memory_peak_bytes if linked_job else None
        ),
        platform_job_memory_limit_bytes=(
            linked_job.memory_limit_bytes if linked_job else None
        ),
    )


@router.get("", response_model=SchedulerDiagnosticsResponse)
async def get_scheduler_diagnostics(
    ctx: Context,
    user: CurrentSuperuser,
) -> SchedulerDiagnosticsResponse:
    now = utcnow()
    lease = await ctx.db.get(SchedulerLease, TRIGGER_LEASE_NAME)
    leader_healthy = bool(
        lease
        and lease.owner_id
        and lease.lease_expires_at
        and lease.lease_expires_at > now
    )
    leader_owner = lease.owner_id if leader_healthy and lease else None

    replica_rows = (
        await ctx.db.execute(
            select(SchedulerReplica)
            .where(
                SchedulerReplica.last_heartbeat_at >= now - REPLICA_STALE_RETENTION
            )
            .order_by(SchedulerReplica.id)
        )
    ).scalars().all()
    active_rows = (
        await ctx.db.execute(
            select(PlatformJob.lease_owner, PlatformJob.id).where(
                PlatformJob.status.in_(("running", "cancel_requested")),
                PlatformJob.lease_owner.is_not(None),
            )
        )
    ).all()
    active_by_owner: dict[str, list] = {}
    for owner, job_id in active_rows:
        active_by_owner.setdefault(owner, []).append(job_id)
    online_cutoff = now - REPLICA_ONLINE_WINDOW
    replicas = [
        SchedulerReplicaStatus(
            id=row.id,
            hostname=row.hostname,
            pid=row.pid,
            job_slots=row.job_slots,
            is_leader=row.id == leader_owner,
            online=row.last_heartbeat_at >= online_cutoff,
            started_at=row.started_at,
            last_heartbeat_at=row.last_heartbeat_at,
            memory_current_bytes=row.memory_current_bytes,
            memory_limit_bytes=row.memory_limit_bytes,
            active_platform_job_ids=active_by_owner.get(row.id, []),
            active_platform_jobs=len(active_by_owner.get(row.id, [])),
        )
        for row in replica_rows
    ]
    online_replicas = [replica for replica in replicas if replica.online]

    state_rows = {
        row.task_id: row
        for row in (
            await ctx.db.execute(select(SchedulerTaskState))
        ).scalars().all()
    }
    last_run_ids = [row.last_run_id for row in state_rows.values() if row.last_run_id]
    run_rows = {}
    if last_run_ids:
        run_rows = {
            row.id: row
            for row in (
                await ctx.db.execute(
                    select(SchedulerTaskRun).where(SchedulerTaskRun.id.in_(last_run_ids))
                )
            ).scalars().all()
        }
    linked_job_ids = [row.platform_job_id for row in run_rows.values() if row.platform_job_id]
    linked_jobs = {}
    if linked_job_ids:
        linked_jobs = {
            row.id: row
            for row in (
                await ctx.db.execute(
                    select(PlatformJob).where(PlatformJob.id.in_(linked_job_ids))
                )
            ).scalars().all()
        }

    tasks: list[SchedulerTaskStatus] = []
    for definition in SCHEDULED_TASKS:
        state = state_rows.get(definition.task_id)
        run = run_rows.get(state.last_run_id) if state and state.last_run_id else None
        last_run = None
        if run is not None:
            last_run = _run_status(run, linked_jobs)
        tasks.append(
            SchedulerTaskStatus(
                task_id=definition.task_id,
                name=state.name if state else definition.name,
                schedule=state.schedule if state else definition.schedule,
                execution_mode=state.execution_mode if state else definition.execution_mode,
                enabled=state.enabled if state else False,
                next_run_at=state.next_run_at if state else None,
                last_run=last_run,
            )
        )

    queued_rows = (
        await ctx.db.execute(
            select(PlatformJob.created_at, PlatformJob.phase).where(
                PlatformJob.status == "queued"
            )
        )
    ).all()
    oldest_queued_seconds = None
    if queued_rows:
        oldest_queued_seconds = max(
            0.0, (now - min(created_at for created_at, _ in queued_rows)).total_seconds()
        )
    utilization = [
        100 * replica.memory_current_bytes / replica.memory_limit_bytes
        for replica in online_replicas
        if replica.memory_current_bytes is not None
        and replica.memory_limit_bytes is not None
        and replica.memory_limit_bytes > 0
    ]
    capacity = SchedulerCapacityStatus(
        replicas_online=len(online_replicas),
        slots_total=sum(replica.job_slots for replica in online_replicas),
        slots_running=sum(replica.active_platform_jobs for replica in online_replicas),
        jobs_queued=len(queued_rows),
        jobs_waiting_for_memory=sum(
            1 for _, phase in queued_rows if phase == "Waiting for scheduler memory"
        ),
        oldest_queued_seconds=oldest_queued_seconds,
        max_memory_utilization_percent=max(utilization) if utilization else None,
    )

    return SchedulerDiagnosticsResponse(
        generated_at=now,
        leader=SchedulerLeaderStatus(
            owner_id=leader_owner,
            lease_expires_at=lease.lease_expires_at if lease else None,
            healthy=leader_healthy,
        ),
        capacity=capacity,
        replicas=replicas,
        tasks=tasks,
    )


@router.get(
    "/tasks/{task_id}/runs",
    response_model=SchedulerTaskHistoryResponse,
)
async def get_scheduler_task_history(
    task_id: str,
    ctx: Context,
    user: CurrentSuperuser,
    limit: int = Query(default=10, ge=1, le=25),
) -> SchedulerTaskHistoryResponse:
    definition = SCHEDULED_TASKS_BY_ID.get(task_id)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scheduled task not found",
        )

    runs = (
        await ctx.db.execute(
            select(SchedulerTaskRun)
            .where(SchedulerTaskRun.task_id == task_id)
            .order_by(SchedulerTaskRun.started_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    platform_job_ids = [run.platform_job_id for run in runs if run.platform_job_id]
    linked_jobs = {}
    if platform_job_ids:
        linked_jobs = {
            job.id: job
            for job in (
                await ctx.db.execute(
                    select(PlatformJob).where(PlatformJob.id.in_(platform_job_ids))
                )
            ).scalars().all()
        }

    run_ids = [run.id for run in runs]
    log_rows = []
    if run_ids:
        log_filter = SystemDiagnosticLog.scheduler_run_id.in_(run_ids)
        if platform_job_ids:
            log_filter = or_(
                log_filter,
                SystemDiagnosticLog.platform_job_id.in_(platform_job_ids),
            )
        log_rows = (
            await ctx.db.execute(
                select(SystemDiagnosticLog)
                .where(log_filter)
                .order_by(
                    SystemDiagnosticLog.created_at.asc(),
                    SystemDiagnosticLog.id.asc(),
                )
            )
        ).scalars().all()

    logs_by_run: dict[UUID, list[SystemDiagnosticLogPublic]] = {
        run.id: [] for run in runs
    }
    run_ids_by_platform_job: dict[UUID, list[UUID]] = {}
    for run in runs:
        if run.platform_job_id:
            run_ids_by_platform_job.setdefault(run.platform_job_id, []).append(run.id)
    for row in log_rows:
        target_run_ids = (
            [row.scheduler_run_id]
            if row.scheduler_run_id
            else (
                run_ids_by_platform_job.get(row.platform_job_id, [])
                if row.platform_job_id
                else []
            )
        )
        for run_id in target_run_ids:
            if run_id not in logs_by_run:
                continue
            logs_by_run[run_id].append(
                SystemDiagnosticLogPublic(
                    id=row.id,
                    source=row.source,
                    level=row.level,
                    code=row.code,
                    message=row.message,
                    scheduler_run_id=row.scheduler_run_id,
                    platform_job_id=row.platform_job_id,
                    created_at=row.created_at,
                )
            )

    return SchedulerTaskHistoryResponse(
        task_id=task_id,
        name=definition.name,
        runs=[
            SchedulerTaskRunDetail(
                **_run_status(run, linked_jobs).model_dump(),
                logs=logs_by_run[run.id],
            )
            for run in runs
        ],
    )
