"""Shared fenced completion path for local and remote Solution app builds."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.contracts.solution_builder import BuildJobStatusUpdate
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.services.builder.staged_artifacts import (
    BuildArtifactIntegrityError,
    StagedBuildArtifactStorage,
)


class BuildCompletionError(RuntimeError):
    """A build completion cannot be accepted in its current state."""


class BuildCompletionMissing(BuildCompletionError):
    pass


class BuildCompletionConflict(BuildCompletionError):
    pass


class BuildCompletionInvalid(BuildCompletionError):
    pass


@dataclass(frozen=True)
class BuildCompletionResult:
    retried: bool = False
    idempotent: bool = False


async def complete_build_attempt(
    db: AsyncSession,
    *,
    job_id: UUID,
    dispatch_attempt: int,
    update: BuildJobStatusUpdate,
) -> BuildCompletionResult:
    """Validate, persist, and finish one fenced build attempt.

    Both the existing Worker and the optional Cloudflare harness terminate
    through this function so manifest validation, log bounding, retries, and
    PlatformJob projection cannot drift by execution provider.
    """
    platform_job = await db.get(PlatformJob, job_id)
    build_job = await db.get(SolutionBuildJob, job_id)
    if platform_job is None or build_job is None:
        raise BuildCompletionMissing("Build job not found")
    if platform_job.attempt != dispatch_attempt:
        raise BuildCompletionConflict("Build completion was fenced out")
    if platform_job.status in {"cancel_requested", "cancelled"}:
        if update.status != "cancelled":
            raise BuildCompletionConflict("Build job was cancelled")
    projection_already_terminal = build_job.status in {
        "succeeded",
        "failed",
        "cancelled",
        "timeout",
    }
    if projection_already_terminal:
        if build_job.status == update.status:
            manifest = build_job.output_manifest
        else:
            raise BuildCompletionConflict("Build job already completed")
    elif build_job.status != "running":
        raise BuildCompletionConflict("Invalid build transition")

    if not projection_already_terminal and update.status == "failed" and update.retryable:
        from src.services.builder.build_requests import retry_external_build_completion

        if await retry_external_build_completion(
            db,
            build_job_id=job_id,
            dispatch_attempt=dispatch_attempt,
            error=update.error or "Build runner failed transiently",
        ):
            return BuildCompletionResult(retried=True)

    if not projection_already_terminal:
        manifest = (
            [entry.model_dump(mode="json") for entry in update.output_manifest]
            if update.output_manifest is not None
            else None
        )
    if not projection_already_terminal and update.status == "succeeded":
        if not manifest or build_job.app_id is None:
            raise BuildCompletionInvalid(
                "A successful build requires an output manifest"
            )
        try:
            await StagedBuildArtifactStorage(job_id).verify_manifest(
                build_job.app_id,
                manifest,
            )
        except (ValueError, BuildArtifactIntegrityError, FileNotFoundError) as exc:
            raise BuildCompletionInvalid(str(exc)) from exc

    if not projection_already_terminal:
        encoded_log = (update.log_excerpt or "").encode("utf-8")[
            -get_settings().builder_log_limit_bytes :
        ]
        build_job.status = update.status
        build_job.error = update.error
        build_job.log_excerpt = encoded_log.decode("utf-8", errors="ignore") or None
        build_job.output_manifest = manifest

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        build_job.last_progress_at = now
        build_job.completed_at = now
        await db.commit()

    from src.services.platform_jobs import finish_external_platform_job

    finished = await finish_external_platform_job(
        job_id,
        dispatch_attempt,
        status=(
            "succeeded"
            if update.status == "succeeded"
            else "cancelled"
            if update.status == "cancelled"
            else "failed"
        ),
        result={
            "build_job_id": str(job_id),
            "output_manifest": manifest,
            "log_excerpt": build_job.log_excerpt,
        },
        error_message=(
            update.error
            if update.status not in {"succeeded", "cancelled"}
            else None
        ),
    )
    if not finished:
        current = await db.get(PlatformJob, job_id)
        if current is None or current.status not in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            raise BuildCompletionConflict("Build completion was fenced out")
    return BuildCompletionResult(idempotent=projection_already_terminal)


__all__ = [
    "BuildCompletionConflict",
    "BuildCompletionError",
    "BuildCompletionInvalid",
    "BuildCompletionMissing",
    "BuildCompletionResult",
    "complete_build_attempt",
]
