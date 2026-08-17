"""PlatformJob control plane for canonical Solution application builds."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobDeferred,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.services.sandbox_runners import (
    SandboxDispatchFailed,
    SandboxRunnerUnavailable,
    cancel_external_sandbox_run,
    dispatch_sandbox_platform_job,
)


class SolutionBuildPayload(BaseModel):
    build_job_id: UUID


async def run_solution_build(
    context: PlatformJobContext,
    payload: SolutionBuildPayload,
) -> dict[str, str]:
    """Validate and dispatch a build, then release scheduler capacity."""
    async with get_db_context() as db:
        build_job = await db.get(
            SolutionBuildJob,
            payload.build_job_id,
            with_for_update=True,
        )
        if build_job is None:
            raise PlatformJobFailure(
                "build_job_missing",
                "The Solution build record no longer exists.",
            )
        if build_job.status == "queued":
            now = datetime.now(timezone.utc)
            build_job.status = "running"
            build_job.started_at = build_job.started_at or now
            build_job.claimed_at = now
            build_job.last_progress_at = now
            await db.commit()
        elif build_job.status != "running":
            raise PlatformJobFailure(
                "build_job_not_runnable",
                f"The Solution build is already {build_job.status}.",
            )
        input_sha256 = build_job.source_sha256

    await context.report("Starting application build", percent=1)
    try:
        dispatch = await dispatch_sandbox_platform_job(
            context.job_id,
            context.lease_token,
            input_sha256=input_sha256,
        )
    except SandboxRunnerUnavailable as exc:
        await _fail_build_projection(payload.build_job_id, str(exc))
        raise PlatformJobFailure(
            "sandbox_runner_unavailable",
            str(exc),
        ) from exc
    except SandboxDispatchFailed as exc:
        await _fail_build_projection(payload.build_job_id, str(exc))
        raise PlatformJobFailure(
            "sandbox_dispatch_failed",
            str(exc),
            retryable=True,
        ) from exc
    if dispatch.cancelled:
        await _cancel_build_projection(payload.build_job_id)
        raise PlatformJobCancelled

    raise PlatformJobDeferred(
        "Application build is running",
        {"build_job_id": str(payload.build_job_id)},
        external_provider=dispatch.provider,
        external_run_id=dispatch.external_run_id,
        external_started_at=dispatch.started_at,
    )


async def _fail_build_projection(build_job_id: UUID, message: str) -> None:
    async with get_db_context() as db:
        build_job = await db.get(SolutionBuildJob, build_job_id, with_for_update=True)
        if build_job is None or build_job.status in {
            "succeeded",
            "failed",
            "cancelled",
            "timeout",
        }:
            return
        build_job.status = "failed"
        build_job.error = message[:4000]
        build_job.completed_at = datetime.now(timezone.utc)
        await db.commit()


async def _cancel_build_projection(build_job_id: UUID) -> None:
    async with get_db_context() as db:
        build_job = await db.get(SolutionBuildJob, build_job_id, with_for_update=True)
        if build_job is None or build_job.status not in {"queued", "running"}:
            return
        build_job.status = "cancelled"
        build_job.completed_at = datetime.now(timezone.utc)
        await db.commit()


async def cancel_solution_build(job_id: UUID, raw_payload: BaseModel) -> None:
    """Synchronize the build projection and notify the selected executor."""
    from src.models.orm.platform_jobs import PlatformJob

    payload = SolutionBuildPayload.model_validate(raw_payload)
    await _cancel_build_projection(payload.build_job_id)
    async with get_db_context() as db:
        platform_job = await db.get(PlatformJob, job_id)
    if platform_job is not None:
        await cancel_external_sandbox_run(platform_job)


SOLUTION_BUILD_DEFINITION = PlatformJobDefinition(
    job_type="solution.build",
    payload_version=1,
    payload_model=SolutionBuildPayload,
    handler=run_solution_build,
    policy=PlatformJobPolicy(
        timeout_seconds=15 * 60,
        max_attempts=3,
        min_memory_headroom_mb=64,
        allow_running_cancellation=True,
    ),
    cancellation_handler=cancel_solution_build,
)


__all__ = [
    "SOLUTION_BUILD_DEFINITION",
    "SolutionBuildPayload",
    "run_solution_build",
]
