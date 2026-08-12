from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.scheduler_diagnostics import (
    SchedulerReplica,
    SchedulerTaskRun,
    SchedulerTaskState,
    SystemDiagnosticLog,
)
from src.routers.scheduler_diagnostics import (
    get_scheduler_diagnostics,
    get_scheduler_task_history,
)
from src.scheduler.registry import SCHEDULED_TASKS_BY_ID, ScheduledTaskDefinition
from src.services import scheduler_diagnostics as diagnostics


@pytest.fixture
def diagnostics_context(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    monkeypatch.setattr(diagnostics, "get_db_context", context)
    monkeypatch.setattr(diagnostics, "get_cgroup_memory", lambda: (600, 1000))


@pytest.mark.asyncio
async def test_records_replica_schedule_run_and_curated_logs(
    db_session: AsyncSession,
    diagnostics_context: None,
) -> None:
    now = datetime.now(timezone.utc)
    suffix = uuid4().hex
    replica_id = f"scheduler-test-{suffix}"
    task_id = f"oauth-token-refresh-test-{suffix}"
    await diagnostics.heartbeat_scheduler_replica(
        replica_id=replica_id,
        hostname="scheduler-1",
        pid=42,
        job_slots=2,
        started_at=now,
    )
    await diagnostics.publish_task_states(
        [
            (
                ScheduledTaskDefinition(
                    task_id,
                    "Refresh Expiring OAuth Tokens",
                    "Every 15 minutes",
                    "durable_job",
                ),
                now + timedelta(minutes=15),
            )
        ]
    )
    run_id = await diagnostics.start_scheduler_run(task_id, replica_id)
    await diagnostics.finish_scheduler_run(
        run_id,
        status="succeeded",
        summary="No tokens required refresh",
    )

    replica = await db_session.get(SchedulerReplica, replica_id)
    assert replica is not None
    assert replica.memory_current_bytes == 600
    assert replica.memory_limit_bytes == 1000
    assert replica.job_slots == 2

    state = await db_session.get(SchedulerTaskState, task_id)
    assert state is not None
    assert state.last_run_id == run_id
    assert state.execution_mode == "durable_job"

    run = await db_session.get(SchedulerTaskRun, run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert run.completed_at is not None
    assert run.duration_ms is not None

    logs = (
        await db_session.execute(
            select(SystemDiagnosticLog)
            .where(SystemDiagnosticLog.scheduler_run_id == run_id)
            .order_by(SystemDiagnosticLog.id)
        )
    ).scalars().all()
    assert [(log.code, log.level) for log in logs] == [
        ("scheduled_task_started", "info"),
        ("scheduled_task_completed", "info"),
    ]
    assert logs[-1].message == "No tokens required refresh"


@pytest.mark.asyncio
async def test_scheduler_snapshot_explains_capacity_pressure(
    db_session: AsyncSession,
    diagnostics_context: None,
) -> None:
    now = datetime.now(timezone.utc)
    replica_id = f"scheduler-test-{uuid4().hex}"
    await diagnostics.heartbeat_scheduler_replica(
        replica_id=replica_id,
        hostname="scheduler-1",
        pid=42,
        job_slots=2,
        started_at=now,
    )
    response = await get_scheduler_diagnostics(
        SimpleNamespace(db=db_session),  # type: ignore[arg-type]
        SimpleNamespace(is_superuser=True),  # type: ignore[arg-type]
    )

    replica = next(item for item in response.replicas if item.id == replica_id)
    assert response.capacity.replicas_online >= 1
    assert response.capacity.slots_total >= 2
    assert response.capacity.max_memory_utilization_percent is not None
    assert response.capacity.max_memory_utilization_percent >= 60
    assert replica.job_slots == 2
    assert replica.active_platform_jobs == 0
    assert {task.task_id for task in response.tasks} >= {
        "oauth_token_refresh",
        "scheduler_diagnostics_cleanup",
    }


@pytest.mark.asyncio
async def test_scheduler_snapshot_drops_expired_replicas(
    db_session: AsyncSession,
    diagnostics_context: None,
) -> None:
    now = datetime.now(timezone.utc)
    stale_id = f"scheduler-stale-{uuid4().hex}"
    expired_id = f"scheduler-expired-{uuid4().hex}"
    db_session.add_all(
        [
            SchedulerReplica(
                id=stale_id,
                hostname="scheduler-stale",
                pid=41,
                job_slots=1,
                started_at=now - timedelta(minutes=2),
                last_heartbeat_at=now - timedelta(minutes=1),
            ),
            SchedulerReplica(
                id=expired_id,
                hostname="scheduler-expired",
                pid=42,
                job_slots=1,
                started_at=now - timedelta(minutes=10),
                last_heartbeat_at=now - timedelta(minutes=6),
            ),
        ]
    )
    await db_session.flush()

    response = await get_scheduler_diagnostics(
        SimpleNamespace(db=db_session),  # type: ignore[arg-type]
        SimpleNamespace(is_superuser=True),  # type: ignore[arg-type]
    )

    replicas = {replica.id: replica for replica in response.replicas}
    assert replicas[stale_id].online is False
    assert expired_id not in replicas

    await diagnostics.heartbeat_scheduler_replica(
        replica_id=f"scheduler-live-{uuid4().hex}",
        hostname="scheduler-live",
        pid=43,
        job_slots=1,
        started_at=now,
    )
    assert await db_session.get(SchedulerReplica, expired_id) is None


@pytest.mark.asyncio
async def test_scheduler_task_history_groups_published_logs_by_run(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    diagnostics_context: None,
) -> None:
    task_id = f"history-test-{uuid4().hex}"
    monkeypatch.setitem(
        SCHEDULED_TASKS_BY_ID,
        task_id,
        ScheduledTaskDefinition(
            task_id,
            "History Test Task",
            "Every 15 minutes",
            "durable_job",
        ),
    )
    first_run_id = await diagnostics.start_scheduler_run(task_id, "scheduler-a")
    await diagnostics.finish_scheduler_run(
        first_run_id,
        status="succeeded",
        summary="First sweep completed",
    )
    second_run_id = await diagnostics.start_scheduler_run(task_id, "scheduler-b")
    await diagnostics.finish_scheduler_run(
        second_run_id,
        status="failed",
        error_message="Second sweep failed",
    )

    response = await get_scheduler_task_history(
        task_id,
        SimpleNamespace(db=db_session),  # type: ignore[arg-type]
        SimpleNamespace(is_superuser=True),  # type: ignore[arg-type]
        limit=2,
    )

    assert response.name == "History Test Task"
    assert [run.id for run in response.runs] == [second_run_id, first_run_id]
    assert [log.code for log in response.runs[0].logs] == [
        "scheduled_task_started",
        "scheduled_task_failed",
    ]
    assert response.runs[0].logs[-1].message == "Second sweep failed"
    assert response.runs[1].logs[-1].message == "First sweep completed"


@pytest.mark.asyncio
async def test_platform_job_deletion_preserves_scheduled_run_logs(
    db_session: AsyncSession,
) -> None:
    job = PlatformJob(
        job_type="diagnostics.retention_test",
        payload_version=1,
        payload={},
        requested_by_user_id="system",
        requested_by_email="system@gobifrost.local",
        requested_by_name="Bifrost Scheduler",
        title="Diagnostics retention test",
        status="succeeded",
    )
    db_session.add(job)
    await db_session.flush()
    run = SchedulerTaskRun(
        task_id="diagnostics_retention_test",
        leader_owner_id="scheduler-a",
        status="succeeded",
        platform_job_id=job.id,
    )
    db_session.add(run)
    await db_session.flush()
    log = SystemDiagnosticLog(
        source="platform_job",
        level="info",
        code="retained_log",
        message="Keep this log for the scheduler retention window",
        scheduler_run_id=run.id,
        platform_job_id=job.id,
    )
    db_session.add(log)
    await db_session.commit()
    log_id = log.id
    run_id = run.id

    await db_session.delete(job)
    await db_session.commit()
    db_session.expire_all()

    retained_log = await db_session.get(SystemDiagnosticLog, log_id)
    assert retained_log is not None
    assert retained_log.scheduler_run_id == run_id
    assert retained_log.platform_job_id is None


@pytest.mark.asyncio
async def test_rejects_unpublished_log_levels(
    diagnostics_context: None,
) -> None:
    with pytest.raises(ValueError, match="unsupported diagnostic log level"):
        await diagnostics.publish_system_diagnostic_log(
            source="platform_job",
            level="secret",
            code="unsafe",
            message="must not publish",
        )
