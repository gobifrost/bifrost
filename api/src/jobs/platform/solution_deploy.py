"""Durable scheduler adapter for all Solution deploy and install variants."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)


class SolutionDeployPayload(BaseModel):
    deploy_job_id: UUID
    kind: Literal["deploy", "install", "install_from_repo"]
    install_id: UUID | None = None
    input_sha256: str
    options: dict[str, Any]


async def run_solution_deploy(
    context: PlatformJobContext,
    payload: SolutionDeployPayload,
) -> dict:
    from src.core.database import get_db_context
    from src.models.orm.solution_deploy_jobs import SolutionDeployJob
    from src.services.solutions.deploy_jobs import execute_deploy_job

    await context.report("Loading staged Solution input", percent=2)
    await execute_deploy_job(payload.deploy_job_id, context.lease_token)
    async with get_db_context() as db:
        projection = await db.get(SolutionDeployJob, payload.deploy_job_id)
        if projection is None:
            raise PlatformJobFailure("deploy_job_missing", "Deploy job is missing.")
        if projection.status != "succeeded":
            raise PlatformJobFailure(
                "solution_deploy_failed",
                projection.error or "Solution deploy failed.",
            )
        result = projection.result or {}
    await context.report("Solution deploy complete", percent=100)
    await context.log(
        "info",
        "solution_deploy_completed",
        f"Solution {payload.kind} job {payload.deploy_job_id} completed",
    )
    return result


SOLUTION_DEPLOY_DEFINITION = PlatformJobDefinition(
    job_type="solution.deploy",
    payload_version=1,
    payload_model=SolutionDeployPayload,
    handler=run_solution_deploy,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=768,
    ),
    encrypt_payload=True,
)
