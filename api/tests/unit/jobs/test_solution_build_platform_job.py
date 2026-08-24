from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobDeferred
from src.jobs.platform.solution_build import (
    SolutionBuildPayload,
    cancel_solution_build,
    run_solution_build,
)
from src.services.sandbox_runners import SandboxDispatchResult


@pytest.mark.asyncio
async def test_solution_build_marks_projection_running_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_job_id = uuid4()
    context = AsyncMock()
    context.job_id = build_job_id
    context.lease_token = uuid4()
    build_job = SimpleNamespace(
        id=build_job_id,
        source_sha256="a" * 64,
        status="queued",
        started_at=None,
        claimed_at=None,
        last_progress_at=None,
    )
    db = AsyncMock()
    db.get.return_value = build_job

    @asynccontextmanager
    async def db_context():
        yield db

    dispatch = AsyncMock(
        return_value=SandboxDispatchResult(
            provider="local",
            external_run_id="local-run",
            started_at=datetime.now(timezone.utc),
        )
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_build.get_db_context",
        db_context,
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_build.dispatch_sandbox_platform_job",
        dispatch,
    )

    with pytest.raises(PlatformJobDeferred, match="Application build is running") as raised:
        await run_solution_build(
            context,
            SolutionBuildPayload(build_job_id=build_job_id),
        )

    assert build_job.status == "running"
    assert build_job.started_at is not None
    db.commit.assert_awaited_once()
    dispatch.assert_awaited_once_with(
        context.job_id,
        context.lease_token,
        input_sha256="a" * 64,
    )
    assert raised.value.external_provider == "local"
    assert raised.value.external_run_id == "local-run"


@pytest.mark.asyncio
async def test_solution_build_cancellation_targets_projection_from_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_job_id = uuid4()
    build_job_id = uuid4()
    platform_job = SimpleNamespace(id=platform_job_id)
    db = AsyncMock()
    db.get.return_value = platform_job

    @asynccontextmanager
    async def db_context():
        yield db

    cancel_projection = AsyncMock()
    cancel_external = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.solution_build.get_db_context", db_context
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_build._cancel_build_projection",
        cancel_projection,
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_build.cancel_external_sandbox_run",
        cancel_external,
    )

    await cancel_solution_build(
        platform_job_id,
        SolutionBuildPayload(build_job_id=build_job_id),
    )

    cancel_projection.assert_awaited_once_with(build_job_id)
    cancel_external.assert_awaited_once_with(platform_job)
