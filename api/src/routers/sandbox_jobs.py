"""Job-bound callback API used by external sandbox runner attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.database import get_db
from src.models.contracts.sandbox_runner import (
    SandboxJobCancelled,
    SandboxJobProgressUpdate,
)
from src.models.contracts.solution_builder import BuildJobStatusUpdate
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.services.builder.capabilities import (
    SANDBOX_ARTIFACT_WRITE,
    SANDBOX_CANCEL_READ,
    SANDBOX_COMPLETE_WRITE,
    SANDBOX_INPUT_READ,
    SANDBOX_PROGRESS_WRITE,
    SandboxJobCapability,
    require_sandbox_job_capability,
)
from src.services.builder.staged_artifacts import (
    BuildArtifactIntegrityError,
    BuildOutputTooLarge,
    StagedBuildArtifactStorage,
)

router = APIRouter(
    prefix="/api/internal/sandbox/jobs",
    tags=["internal-sandbox-jobs"],
    include_in_schema=False,
)
Db = Annotated[AsyncSession, Depends(get_db)]


async def _capability(
    job_id: UUID,
    db: Db,
    authorization: Annotated[str | None, Header()] = None,
) -> SandboxJobCapability:
    return await require_sandbox_job_capability(job_id, db, authorization)


Capability = Annotated[SandboxJobCapability, Depends(_capability)]


def _require_build(capability: SandboxJobCapability) -> None:
    if capability.job_type != "solution.build":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This callback is not valid for the sandbox job type",
        )


@router.get("/{job_id}/input")
async def get_input(
    job_id: UUID,
    db: Db,
    capability: Capability,
) -> StreamingResponse:
    capability.require(SANDBOX_INPUT_READ)
    _require_build(capability)
    build_job = await db.get(SolutionBuildJob, job_id)
    if build_job is None or build_job.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job is not running",
        )
    return StreamingResponse(
        StagedBuildArtifactStorage(job_id).open_input_stream(),
        media_type="application/zip",
    )


@router.put("/{job_id}/artifacts/{rel_path:path}")
async def put_artifact(
    job_id: UUID,
    rel_path: str,
    request: Request,
    db: Db,
    capability: Capability,
) -> dict[str, str | int]:
    capability.require(SANDBOX_ARTIFACT_WRITE)
    _require_build(capability)
    build_job = await db.get(SolutionBuildJob, job_id)
    if build_job is None or build_job.status != "running" or build_job.app_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job is not running",
        )
    try:
        digest, size = await StagedBuildArtifactStorage(job_id).write_output(
            build_job.app_id,
            rel_path,
            request.stream(),
            get_settings().builder_output_limit_bytes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except BuildOutputTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    return {"sha256": digest, "size": size}


@router.post("/{job_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
async def progress(
    job_id: UUID,
    body: SandboxJobProgressUpdate,
    db: Db,
    capability: Capability,
) -> Response:
    capability.require(SANDBOX_PROGRESS_WRITE)
    build_job = await db.get(SolutionBuildJob, job_id)
    if capability.job_type == "solution.build":
        if build_job is None or build_job.status != "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Build job is not running",
            )
        build_job.last_progress_at = datetime.now(timezone.utc)
        await db.commit()

    from src.services.platform_jobs import update_external_platform_job_progress

    updated = await update_external_platform_job_progress(
        job_id,
        capability.dispatch_attempt,
        phase=body.phase,
        current=body.current,
        total=body.total,
        percent=body.percent,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sandbox job is no longer running",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{job_id}/cancelled", response_model=SandboxJobCancelled)
async def cancelled(
    job_id: UUID,
    db: Db,
    capability: Capability,
) -> SandboxJobCancelled:
    capability.require(SANDBOX_CANCEL_READ)
    job = await db.get(PlatformJob, job_id)
    return SandboxJobCancelled(
        cancelled=job is None or job.status in {"cancel_requested", "cancelled"}
    )


@router.post("/{job_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_build(
    job_id: UUID,
    body: BuildJobStatusUpdate,
    db: Db,
    capability: Capability,
) -> Response:
    capability.require(SANDBOX_COMPLETE_WRITE)
    _require_build(capability)
    platform_job = await db.get(PlatformJob, job_id)
    build_job = await db.get(SolutionBuildJob, job_id)
    if platform_job is None or build_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build job not found")
    if platform_job.status in {"cancel_requested", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job was cancelled",
        )
    if build_job.status in {"succeeded", "failed", "cancelled", "timeout"}:
        if build_job.status == body.status:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job already completed",
        )
    if build_job.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invalid build transition",
        )

    manifest = (
        [entry.model_dump(mode="json") for entry in body.output_manifest]
        if body.output_manifest is not None
        else None
    )
    if body.status == "succeeded":
        if not manifest or build_job.app_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A successful build requires an output manifest",
            )
        try:
            await StagedBuildArtifactStorage(job_id).verify_manifest(
                build_job.app_id,
                manifest,
            )
        except (ValueError, BuildArtifactIntegrityError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    encoded_log = (body.log_excerpt or "").encode("utf-8")[
        : get_settings().builder_log_limit_bytes
    ]
    build_job.status = body.status
    build_job.error = body.error
    build_job.log_excerpt = encoded_log.decode("utf-8", errors="ignore") or None
    build_job.output_manifest = manifest
    now = datetime.now(timezone.utc)
    build_job.last_progress_at = now
    build_job.completed_at = now
    await db.commit()

    from src.services.platform_jobs import finish_external_platform_job

    result = {
        "build_job_id": str(job_id),
        "output_manifest": manifest,
        "log_excerpt": build_job.log_excerpt,
    }
    finished = await finish_external_platform_job(
        job_id,
        capability.dispatch_attempt,
        status=(
            "succeeded"
            if body.status == "succeeded"
            else "cancelled"
            if body.status == "cancelled"
            else "failed"
        ),
        result=result,
        error_message=(
            body.error if body.status not in {"succeeded", "cancelled"} else None
        ),
    )
    if not finished:
        current = await db.get(PlatformJob, job_id)
        if current is None or current.status not in {"succeeded", "failed", "cancelled"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Sandbox job completion was fenced out",
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
