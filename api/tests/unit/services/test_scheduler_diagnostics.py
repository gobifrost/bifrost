from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.scheduler_diagnostics import (
    SchedulerReplica,
    SchedulerTaskRun,
    SchedulerTaskState,
    SystemDiagnosticLog,
)
from src.routers.scheduler_diagnostics import get_scheduler_diagnostics
from src.scheduler.registry import ScheduledTaskDefinition
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
                    "Refresh expiring OAuth tokens",
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
        log_limit=10,
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
