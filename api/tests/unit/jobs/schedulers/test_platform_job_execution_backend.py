from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform.application_publish import (
    APPLICATION_PUBLISH_DEFINITION,
    ApplicationPublishPayload,
)
from src.jobs.schedulers import platform_jobs as scheduler
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import enqueue_platform_job


AMPLE = 2**40


@pytest.fixture(autouse=True)
def patch_context(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(scheduler, "get_db_context", test_context)


async def _queued_job(db_session: AsyncSession) -> PlatformJob:
    await db_session.execute(delete(PlatformJob))
    await db_session.commit()
    job, _ = await enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=uuid4()),
        dedupe_key=str(uuid4()),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(uuid4()),
        title="Publishing Test",
        action_url=None,
    )
    return job


class _RemoteProcess:
    returncode = None

    def __init__(self) -> None:
        self.terminate = AsyncMock()

    async def wait(self) -> int:
        await asyncio.Future()
        raise AssertionError("unreachable")


class _RemoteBackend:
    def __init__(self, process: _RemoteProcess, memory: tuple[int, int]) -> None:
        self.process = process
        self.spawn = AsyncMock(return_value=process)
        self.memory = MagicMock(return_value=memory)


@pytest.mark.asyncio
async def test_remote_backend_memory_stops_only_the_remote_process(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "RUNNER_RETRY_DELAY", timedelta(hours=1))
    job = await _queued_job(db_session)
    claim = await scheduler.claim_platform_job(
        fits_bytes=AMPLE,
        memory_sample=(0, 100),
    )
    assert claim is not None
    backend = _RemoteBackend(_RemoteProcess(), (97, 100))
    killpg = MagicMock()

    monkeypatch.setattr(scheduler, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(scheduler, "get_cgroup_memory", lambda: (-1, -1))
    monkeypatch.setattr(scheduler, "_monotonic", MagicMock(side_effect=[0, 0]))
    monkeypatch.setattr(scheduler.os, "killpg", killpg)

    assert not await scheduler.run_claimed_platform_job(claim, backend=backend)

    await db_session.refresh(job)
    assert job.status == "queued"
    assert job.error_code == "memory_pressure"
    assert job.memory_peak_bytes == 97
    assert job.memory_limit_bytes == 100
    backend.process.terminate.assert_awaited_once()
    killpg.assert_not_called()


@pytest.mark.asyncio
async def test_remote_backend_cancellation_uses_process_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _RemoteProcess()
    backend = _RemoteBackend(process, (0, 0))
    handle_loss = AsyncMock()
    monkeypatch.setattr(scheduler, "_handle_runner_loss", handle_loss)
    monkeypatch.setattr(scheduler, "_heartbeat", AsyncMock(return_value="cancel_requested"))
    monkeypatch.setattr(scheduler, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
    claim = scheduler.ClaimedPlatformJob(uuid4(), uuid4(), 60, 0.95, 0)

    assert await scheduler.run_claimed_platform_job(claim, backend=backend)

    process.terminate.assert_awaited_once()
    handle_loss.assert_awaited_once_with(
        claim.id,
        claim.lease_token,
        error_code="cancelled",
        error_message="Platform job was cancelled.",
        memory_sample=(0, 0),
    )


@pytest.mark.asyncio
async def test_remote_backend_timeout_uses_process_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _RemoteProcess()
    backend = _RemoteBackend(process, (0, 0))
    handle_loss = AsyncMock()
    monkeypatch.setattr(scheduler, "_handle_runner_loss", handle_loss)
    monkeypatch.setattr(scheduler, "_heartbeat", AsyncMock(return_value="running"))
    monkeypatch.setattr(scheduler, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(scheduler, "_monotonic", MagicMock(side_effect=[0, 61]))
    claim = scheduler.ClaimedPlatformJob(uuid4(), uuid4(), 60, 0.95, 0)

    assert not await scheduler.run_claimed_platform_job(claim, backend=backend)

    process.terminate.assert_awaited_once()
    handle_loss.assert_awaited_once_with(
        claim.id,
        claim.lease_token,
        error_code="timeout",
        error_message="Platform job timed out after 60 seconds.",
        memory_sample=(0, 0),
    )
