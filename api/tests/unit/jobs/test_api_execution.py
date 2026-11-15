from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_api_execution_backend_runs_handler_in_process_and_settles_cancellation(monkeypatch) -> None:
    from src.jobs.platform import api_execution
    from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run(job_id, lease_token):
        assert job_id == claim.id
        assert lease_token == claim.lease_token
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(api_execution, "run_claimed_platform_job", run)
    claim = ClaimedPlatformJob(
        id=uuid4(), lease_token=uuid4(), timeout_seconds=60,
        hard_memory_ratio=0.95, memory_required_bytes=128, job_type="oauth.refresh",
    )

    process = await api_execution.ApiPlatformJobExecutionBackend().spawn(claim)
    await asyncio.wait_for(started.wait(), timeout=1)
    await process.terminate()

    assert await asyncio.wait_for(process.wait(), timeout=1) == 1
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_api_execution_waiter_cancellation_does_not_cancel_handler(monkeypatch) -> None:
    from src.jobs.platform import api_execution
    from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob

    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()
    finished = asyncio.Event()

    async def run(_job_id, _lease_token):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finished.set()
        return True

    monkeypatch.setattr(api_execution, "run_claimed_platform_job", run)
    claim = ClaimedPlatformJob(
        id=uuid4(), lease_token=uuid4(), timeout_seconds=60,
        hard_memory_ratio=0.95, memory_required_bytes=128, job_type="oauth.refresh",
    )
    process = await api_execution.ApiPlatformJobExecutionBackend().spawn(claim)
    await started.wait()

    waiter = asyncio.create_task(process.wait())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert not cancelled.is_set()
    assert process.returncode is None
    release.set()
    assert await process.wait() == 0
    assert finished.is_set()


@pytest.mark.asyncio
async def test_api_execution_backend_refuses_non_api_job_types(monkeypatch) -> None:
    from src.jobs.platform import api_execution
    from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob

    monkeypatch.setattr(api_execution, "run_claimed_platform_job", AsyncMock(return_value=True))
    claim = ClaimedPlatformJob(
        id=uuid4(), lease_token=uuid4(), timeout_seconds=60,
        hard_memory_ratio=0.95, memory_required_bytes=128,
        job_type="application.publish",
    )

    with pytest.raises(ValueError, match="not API-owned"):
        await api_execution.ApiPlatformJobExecutionBackend().spawn(claim)
