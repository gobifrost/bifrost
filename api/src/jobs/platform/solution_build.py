"""Central orchestration envelope for credential-isolated app builds."""

from uuid import UUID

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobDeferred,
    PlatformJobFailure,
    PlatformJobPolicy,
)


class SolutionBuildPayload(BaseModel):
    build_job_id: UUID


async def reconcile_solution_build_jobs() -> int:
    """Finish central parents when a very fast builder won the defer race."""
    from sqlalchemy import select

    from src.core.database import get_db_context
    from src.models.orm.platform_jobs import PlatformJob
    from src.models.orm.solution_build_jobs import SolutionBuildJob
    from src.services.platform_jobs import finish_deferred_platform_job

    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(SolutionBuildJob)
                .join(PlatformJob, PlatformJob.id == SolutionBuildJob.id)
                .where(
                    PlatformJob.status == "waiting",
                    SolutionBuildJob.status.in_(
                        ("succeeded", "failed", "cancelled", "timeout")
                    ),
                )
            )
        ).scalars().all()
    for row in rows:
        await finish_deferred_platform_job(
            row.id,
            status=(
                "succeeded"
                if row.status == "succeeded"
                else "cancelled"
                if row.status == "cancelled"
                else "failed"
            ),
            result={
                "build_job_id": str(row.id),
                "output_manifest": row.output_manifest,
                "log_excerpt": row.log_excerpt,
            },
            error_message=row.error if row.status in {"failed", "timeout"} else None,
        )
    return len(rows)


async def run_solution_build(
    context: PlatformJobContext,
    payload: SolutionBuildPayload,
) -> dict:
    from datetime import datetime, timezone

    from src.core.database import get_db_context
    from src.models.orm.solution_build_jobs import SolutionBuildJob
    from src.services.sandbox_runners import (
        SandboxDispatchFailed,
        SandboxRunnerUnavailable,
        dispatch_sandbox_platform_job,
    )

    async with get_db_context() as db:
        build_job = await db.get(SolutionBuildJob, payload.build_job_id)
        if build_job is None:
            raise PlatformJobFailure(
                "build_job_missing",
                "The Solution build record no longer exists.",
            )
        input_sha256 = build_job.source_sha256

    await context.report("Dispatching to isolated build runner", percent=1)
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
            retryable=False,
        ) from exc
    except SandboxDispatchFailed as exc:
        await _fail_build_projection(payload.build_job_id, str(exc))
        raise PlatformJobFailure(
            "sandbox_dispatch_failed",
            str(exc),
            retryable=True,
        ) from exc

    async with get_db_context() as db:
        build_job = await db.get(
            SolutionBuildJob,
            payload.build_job_id,
            with_for_update=True,
        )
        if build_job is None:
            raise PlatformJobFailure(
                "build_job_missing",
                "The Solution build record disappeared during dispatch.",
            )
        if build_job.status == "queued":
            now = datetime.now(timezone.utc)
            build_job.status = "running"
            build_job.started_at = now
            build_job.claimed_at = now
            build_job.last_progress_at = now
            await db.commit()

    raise PlatformJobDeferred(
        "Waiting for isolated build runner",
        {"build_job_id": str(payload.build_job_id)},
        external_provider=dispatch.provider,
        external_run_id=dispatch.external_run_id,
        external_started_at=dispatch.started_at,
    )


async def _fail_build_projection(build_job_id: UUID, message: str) -> None:
    from datetime import datetime, timezone

    from src.core.database import get_db_context
    from src.models.orm.solution_build_jobs import SolutionBuildJob

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


SOLUTION_BUILD_DEFINITION = PlatformJobDefinition(
    job_type="solution.build",
    payload_version=1,
    payload_model=SolutionBuildPayload,
    handler=run_solution_build,
    policy=PlatformJobPolicy(
        timeout_seconds=5 * 60,
        max_attempts=3,
        min_memory_headroom_mb=64,
        allow_running_cancellation=True,
    ),
)
