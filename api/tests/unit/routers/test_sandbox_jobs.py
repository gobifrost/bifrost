"""Callback-router tests for job-bound sandbox builds."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.solution_builder import (
    BuildJobStatusUpdate,
    BuildOutputEntry,
)
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.routers.sandbox_jobs import complete_build
from src.services.builder.capabilities import (
    SANDBOX_JOB_OPERATIONS,
    SandboxJobCapability,
)


def _capability(job_id):
    return SandboxJobCapability(
        job_id=job_id,
        job_type="solution.build",
        dispatch_attempt=2,
        operations=SANDBOX_JOB_OPERATIONS["solution.build"],
        token_id="token-id",
    )


@pytest.mark.asyncio
async def test_successful_build_verifies_manifest_and_finishes_platform_job():
    job_id = uuid4()
    app_id = uuid4()
    platform_job = SimpleNamespace(id=job_id, status="waiting")
    build_job = SimpleNamespace(
        id=job_id,
        app_id=app_id,
        status="running",
        error=None,
        log_excerpt=None,
        output_manifest=None,
        last_progress_at=None,
        completed_at=None,
    )
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        return platform_job if model is PlatformJob else build_job

    db.get.side_effect = get
    storage = MagicMock()
    storage.verify_manifest = AsyncMock()
    finish = AsyncMock(return_value=True)
    manifest = [
        BuildOutputEntry(path="assets/index.js", sha256="a" * 64, size=42)
    ]

    with (
        patch("src.routers.sandbox_jobs.StagedBuildArtifactStorage", return_value=storage),
        patch("src.services.platform_jobs.finish_external_platform_job", finish),
    ):
        response = await complete_build(
            job_id,
            BuildJobStatusUpdate(
                status="succeeded",
                output_manifest=manifest,
                log_excerpt="built",
            ),
            db,
            _capability(job_id),
        )

    assert response.status_code == 204
    storage.verify_manifest.assert_awaited_once_with(
        app_id,
        [{"path": "assets/index.js", "sha256": "a" * 64, "size": 42}],
    )
    assert build_job.status == "succeeded"
    db.commit.assert_awaited_once()
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == "succeeded"


@pytest.mark.asyncio
async def test_successful_build_requires_a_verified_manifest():
    job_id = uuid4()
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        if model is PlatformJob:
            return SimpleNamespace(id=job_id, status="waiting")
        if model is SolutionBuildJob:
            return SimpleNamespace(id=job_id, app_id=uuid4(), status="running")
        return None

    db.get.side_effect = get

    with pytest.raises(HTTPException) as raised:
        await complete_build(
            job_id,
            BuildJobStatusUpdate(status="succeeded", output_manifest=None),
            db,
            _capability(job_id),
        )

    assert raised.value.status_code == 400
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_is_idempotent_for_matching_terminal_projection():
    job_id = uuid4()
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        if model is PlatformJob:
            return SimpleNamespace(id=job_id, status="succeeded")
        return SimpleNamespace(id=job_id, app_id=uuid4(), status="succeeded")

    db.get.side_effect = get
    response = await complete_build(
        job_id,
        BuildJobStatusUpdate(status="succeeded", output_manifest=None),
        db,
        _capability(job_id),
    )

    assert response.status_code == 204
    db.commit.assert_not_awaited()
