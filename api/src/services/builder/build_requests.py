"""Durable app-build request, reuse, waiting, and cancellation services."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from src.config import get_settings
from src.core.database import get_db_context
from src.core.redis_client import get_redis_client
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder.build_input import make_input_zip
from src.services.builder.build_plane import (
    TOOLCHAIN_VERSION,
    BuildPlaneUnavailable,
    build_plane_available,
    cancel_key,
)
from src.services.builder.staged_artifacts import StagedBuildArtifactStorage

BUILD_QUEUE = "solution-builds"
TERMINAL_BUILD_STATUSES = {"succeeded", "failed", "cancelled", "timeout"}


class BuildFailed(Exception):
    """One or more requested builds ended without a usable artifact."""

    def __init__(self, job: SolutionBuildJob):
        self.job_id = job.id
        self.status = job.status
        self.log_excerpt = job.log_excerpt
        super().__init__(job.error or f"build {job.id} ended as {job.status}")


def _dependency_digest(dependencies: dict[str, str]) -> str:
    encoded = json.dumps(dependencies, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def request_app_build(
    *,
    solution_id: UUID,
    app_id: UUID,
    requested_by: UUID | None,
    src_files: dict[str, bytes],
    dependencies: dict[str, str],
    source_revision_id: UUID | None = None,
) -> SolutionBuildJob:
    """Materialize, stage, commit, and kick one build job.

    This function owns a dedicated transaction. Build rows must become visible
    to the coordinator before any deploy transaction begins waiting for them.
    """
    if not await build_plane_available():
        raise BuildPlaneUnavailable("No builder coordinator is available")

    with tempfile.TemporaryDirectory(prefix=f"bifrost-build-request-{app_id}-") as tmp:
        input_path = Path(tmp) / "input.zip"
        source_sha = make_input_zip(
            input_path,
            app_id,
            src_files,
            dependencies,
            solution_id=solution_id,
        )
        dep_digest = _dependency_digest(dependencies)

        async with get_db_context() as db:
            reusable = (
                await db.execute(
                    select(SolutionBuildJob)
                    .where(
                        SolutionBuildJob.app_id == app_id,
                        SolutionBuildJob.source_sha256 == source_sha,
                        SolutionBuildJob.toolchain_version == TOOLCHAIN_VERSION,
                        SolutionBuildJob.status == "succeeded",
                    )
                    .order_by(SolutionBuildJob.completed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if reusable is not None and reusable.output_manifest:
                try:
                    await StagedBuildArtifactStorage(reusable.id).verify_manifest(
                        app_id,
                        reusable.output_manifest,
                    )
                except (FileNotFoundError, ValueError):
                    reusable = None
            if reusable is not None:
                return reusable

            job_id = uuid4()
            job = SolutionBuildJob(
                id=job_id,
                solution_id=solution_id,
                app_id=app_id,
                source_revision_id=source_revision_id,
                requested_by=requested_by,
                source_sha256=source_sha,
                toolchain_version=TOOLCHAIN_VERSION,
                dependency_digest=dep_digest,
                status="queued",
            )
            db.add(job)
            await db.flush()
            staged_sha = await StagedBuildArtifactStorage(job.id).write_input(input_path)
            if staged_sha != source_sha:
                raise RuntimeError("staged build input hash mismatch")

            from src.jobs.platform.solution_build import (
                SOLUTION_BUILD_DEFINITION,
                SolutionBuildPayload,
            )
            from src.services.platform_jobs import enqueue_platform_job

            solution = await db.get(Solution, solution_id)
            requester = await db.get(User, requested_by) if requested_by else None
            platform_job, _ = await enqueue_platform_job(
                db,
                SOLUTION_BUILD_DEFINITION,
                SolutionBuildPayload(build_job_id=job_id),
                dedupe_key=str(job_id),
                resource_lock_key=f"application:{app_id}",
                priority=300,
                organization_id=solution.organization_id if solution else None,
                requested_by_user_id=requested_by or "system",
                requested_by_email=(
                    requester.email if requester else "system@gobifrost.local"
                ),
                requested_by_name=(
                    requester.name or requester.email
                    if requester
                    else "Bifrost Builder"
                ),
                resource_type="solution_build",
                resource_id=str(job_id),
                title="Building Solution application",
                action_url=f"/solutions/{solution_id}",
                job_id=job_id,
            )

    from src.services.platform_jobs import publish_platform_job_update

    await publish_platform_job_update(platform_job)
    return job


async def load_build_jobs(job_ids: list[UUID]) -> list[SolutionBuildJob]:
    if not job_ids:
        return []
    async with get_db_context() as db:
        rows = (
            (
                await db.execute(
                    select(SolutionBuildJob).where(SolutionBuildJob.id.in_(job_ids))
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in rows}
        return [by_id[job_id] for job_id in job_ids if job_id in by_id]


async def await_build_jobs(
    jobs: list[SolutionBuildJob],
    *,
    timeout_s: float | None = None,
) -> list[SolutionBuildJob]:
    """Poll durable rows until every job succeeds; raise on a terminal failure."""
    if not jobs:
        return []
    deadline = asyncio.get_running_loop().time() + (
        timeout_s
        if timeout_s is not None
        else get_settings().builder_build_timeout_s + 120
    )
    ids = [job.id for job in jobs]
    while True:
        current = await load_build_jobs(ids)
        if len(current) != len(ids):
            raise RuntimeError("build job disappeared while waiting")
        failed = next(
            (job for job in current if job.status in TERMINAL_BUILD_STATUSES - {"succeeded"}),
            None,
        )
        if failed is not None:
            raise BuildFailed(failed)
        if all(job.status == "succeeded" for job in current):
            return current
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("timed out waiting for build jobs")
        await asyncio.sleep(0.5)


async def cancel_build_job(job_id: UUID) -> SolutionBuildJob:
    """Cancel queued work immediately or signal a running coordinator."""
    async with get_db_context() as db:
        job = await db.get(SolutionBuildJob, job_id, with_for_update=True)
        if job is None:
            raise LookupError(job_id)
        if job.status == "queued":
            job.status = "cancelled"
            job.completed_at = datetime.now(timezone.utc)
        elif job.status == "running":
            redis = await get_redis_client()._get_redis()
            await redis.set(
                cancel_key(job.id),
                "1",
                ex=get_settings().builder_build_timeout_s + 120,
            )
        from src.models.orm.platform_jobs import PlatformJob
        from src.services.platform_jobs import request_platform_job_cancel

        platform_job = await db.get(PlatformJob, job_id)
        if platform_job is not None:
            await request_platform_job_cancel(db, platform_job)
        return job
