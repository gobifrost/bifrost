"""Durable scheduler adapter for all Solution deploy and install variants."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob

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
    from src.services.solutions.deploy_jobs import execute_deploy_job

    await context.report("Loading staged Solution input", percent=2)
    await execute_deploy_job(payload.deploy_job_id, context.lease_token)
    failure: PlatformJobFailure | None = None
    result: dict[str, Any] = {}
    async with get_db_context() as db:
        projection = await db.get(SolutionDeployJob, payload.deploy_job_id)
        if projection is None:
            raise PlatformJobFailure("deploy_job_missing", "Deploy job is missing.")
        if projection.status != "succeeded":
            failure = PlatformJobFailure(
                "solution_deploy_failed",
                projection.error or "Solution deploy failed.",
            )
        else:
            result = projection.result or {}
    # execute_deploy_job may have detached and removed a failed repo install.
    # Leave the database context normally before projecting that failure onto
    # the PlatformJob so its cleanup transaction can never be rolled back by
    # this adapter's exception path.
    if failure is not None:
        raise failure
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
        min_memory_headroom_mb=768,
    ),
    encrypt_payload=True,
)
