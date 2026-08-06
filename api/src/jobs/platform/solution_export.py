"""Platform-job adapter for durable Solution backup exports."""

from uuid import UUID

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)


class SolutionExportPayload(BaseModel):
    export_job_id: UUID


async def run_solution_export(
    context: PlatformJobContext,
    payload: SolutionExportPayload,
) -> dict:
    from src.jobs.schedulers.solution_export_jobs import run_solution_export_job

    await context.report("Building Solution backup", percent=5)
    if not await run_solution_export_job(payload.export_job_id):
        raise PlatformJobFailure(
            "solution_export_failed",
            "Solution backup export failed; see the export job for details.",
        )
    await context.report("Solution backup ready", percent=100)
    await context.log(
        "info",
        "solution_export_completed",
        f"Solution export {payload.export_job_id} completed",
    )
    return {"export_job_id": str(payload.export_job_id)}


SOLUTION_EXPORT_DEFINITION = PlatformJobDefinition(
    job_type="solution.export",
    payload_version=1,
    payload_model=SolutionExportPayload,
    handler=run_solution_export,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        min_memory_headroom_mb=512,
    ),
)
