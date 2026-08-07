from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobDeferred
from src.jobs.platform.solution_build import (
    SolutionBuildPayload,
    run_solution_build,
)
from src.services.sandbox_runners import SandboxDispatchResult


@pytest.mark.asyncio
async def test_solution_build_dispatches_exact_job_then_releases_slot() -> None:
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

    dispatch_result = SandboxDispatchResult(
        provider="cloudflare",
        external_run_id="run-123",
        started_at=datetime.now(timezone.utc),
    )
    dispatch = AsyncMock(return_value=dispatch_result)

    with (
        patch("src.core.database.get_db_context", db_context),
        patch(
            "src.services.sandbox_runners.dispatch_sandbox_platform_job",
            dispatch,
        ),
        pytest.raises(PlatformJobDeferred, match="isolated build runner") as raised,
    ):
        await run_solution_build(context, SolutionBuildPayload(build_job_id=build_job_id))

    dispatch.assert_awaited_once_with(
        context.job_id,
        context.lease_token,
        input_sha256="a" * 64,
    )
    assert raised.value.external_provider == "cloudflare"
    assert raised.value.external_run_id == "run-123"
    assert build_job.status == "running"
    db.commit.assert_awaited_once()
    context.report.assert_awaited_once()
