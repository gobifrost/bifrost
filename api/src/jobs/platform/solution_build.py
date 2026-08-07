"""Central orchestration envelope for credential-isolated app builds."""

from uuid import UUID

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobDeferred,
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
    from src.jobs.rabbitmq import publish_message
    from src.services.builder.build_requests import BUILD_QUEUE

    await context.report("Dispatching to isolated build runner", percent=1)
    await publish_message(BUILD_QUEUE, {"job_id": str(payload.build_job_id)})
    raise PlatformJobDeferred(
        "Waiting for isolated build runner",
        {"build_job_id": str(payload.build_job_id)},
    )


SOLUTION_BUILD_DEFINITION = PlatformJobDefinition(
    job_type="solution.build",
    payload_version=1,
    payload_model=SolutionBuildPayload,
    handler=run_solution_build,
    policy=PlatformJobPolicy(
        timeout_seconds=5 * 60,
        max_attempts=3,
        min_memory_headroom_mb=64,
    ),
)
