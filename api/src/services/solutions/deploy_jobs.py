"""Create scheduler-owned Solution deploy jobs from staged archives."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform.solution_deploy import (
    SOLUTION_DEPLOY_DEFINITION,
    SolutionDeployPayload,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.services.platform_jobs import (
    enqueue_platform_job,
    publish_platform_job_update,
)
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage

SolutionDeployKind = Literal["deploy", "install", "install_from_repo"]


async def create_staged_deploy_job(
    db: AsyncSession,
    *,
    kind: SolutionDeployKind,
    install_id: UUID | None,
    organization_id: UUID | None,
    requested_by_user_id: UUID | str,
    requested_by_email: str,
    requested_by_name: str,
    options: dict[str, Any],
    input_path: Path | None = None,
    input_bytes: bytes | None = None,
    memory_profile_key: str | None = None,
) -> SolutionDeployJob:
    """Stage one validated archive and atomically expose its durable job."""
    if (input_path is None) == (input_bytes is None):
        raise ValueError("exactly one staged input is required")

    job_id = uuid4()
    storage = SolutionDeployJobStorage(job_id)
    if input_path is not None:
        digest, _ = await storage.write_path(input_path)
    else:
        assert input_bytes is not None
        digest, _ = await storage.write_bytes(input_bytes)

    projection = SolutionDeployJob(
        id=job_id,
        install_id=install_id,
        status="queued",
    )
    db.add(projection)
    try:
        platform_job, _ = await enqueue_platform_job(
            db,
            SOLUTION_DEPLOY_DEFINITION,
            SolutionDeployPayload(
                deploy_job_id=job_id,
                kind=kind,
                install_id=install_id,
                input_sha256=digest,
                options=options,
            ),
            dedupe_key=str(job_id),
            resource_lock_key=f"solution:{install_id}" if install_id else None,
            priority=500,
            organization_id=organization_id,
            requested_by_user_id=requested_by_user_id,
            requested_by_email=requested_by_email,
            requested_by_name=requested_by_name,
            resource_type="solution_deploy",
            resource_id=str(job_id),
            title=f"Solution {kind.replace('_', ' ')}",
            action_url=(f"/solutions/{install_id}" if install_id else "/solutions"),
            job_id=job_id,
            memory_profile_key=memory_profile_key,
        )
        await db.commit()
        await db.refresh(projection)
    except Exception:
        await db.rollback()
        await storage.delete()
        raise

    await publish_platform_job_update(platform_job)
    return projection
