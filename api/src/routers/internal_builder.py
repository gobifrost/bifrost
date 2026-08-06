"""Internal-only streaming API for the credential-light build coordinator."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.database import get_db
from src.core.redis_client import get_redis_client
from src.models.contracts.solution_builder import BuildJobStatusUpdate, ClaimedBuildJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.services.builder.build_plane import record_builder_heartbeat
from src.services.builder.build_plane import cancel_key
from src.services.builder.capabilities import mint_build_capability, require_build_capability
from src.services.builder.claim import claim_build_job
from src.services.builder.staged_artifacts import (
    BuildArtifactIntegrityError,
    BuildOutputTooLarge,
    StagedBuildArtifactStorage,
)

router = APIRouter(
    prefix="/api/internal/builder",
    tags=["internal-builder"],
    include_in_schema=False,
)
Db = Annotated[AsyncSession, Depends(get_db)]
Capability = Annotated[dict[str, Any], Depends(require_build_capability)]


def _require_internal_secret(value: str | None) -> None:
    expected = get_settings().builder_internal_secret
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Builder internal API is disabled",
        )
    if value is None or not secrets.compare_digest(value, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid builder key",
        )


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(
    key: Annotated[str | None, Header(alias="X-Bifrost-Builder-Key")] = None,
) -> Response:
    _require_internal_secret(key)
    await record_builder_heartbeat()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/claim")
async def claim(
    job_id: UUID,
    db: Db,
    key: Annotated[str | None, Header(alias="X-Bifrost-Builder-Key")] = None,
) -> dict[str, Any]:
    _require_internal_secret(key)
    job = await claim_build_job(db, job_id)
    if job is None:
        return {"job": None, "capability": None}
    if job.app_id is None:
        job.status = "failed"
        job.error = "build job has no app id"
        job.completed_at = datetime.now(timezone.utc)
        return {"job": None, "capability": None}
    public = ClaimedBuildJob(
        id=job.id,
        solution_id=job.solution_id,
        app_id=job.app_id,
        timeout_s=get_settings().builder_build_timeout_s,
    )
    return {
        "job": public.model_dump(mode="json"),
        "capability": mint_build_capability(job),
    }


@router.get("/jobs/{job_id}/input")
async def get_input(
    job_id: UUID,
    _capability: Capability,
) -> StreamingResponse:
    return StreamingResponse(
        StagedBuildArtifactStorage(job_id).open_input_stream(),
        media_type="application/zip",
    )


@router.put("/jobs/{job_id}/artifacts/{rel_path:path}")
async def put_artifact(
    job_id: UUID,
    rel_path: str,
    request: Request,
    db: Db,
    _capability: Capability,
) -> dict[str, Any]:
    job = await db.get(SolutionBuildJob, job_id)
    if job is None or job.status != "running" or job.app_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job is not running",
        )
    try:
        digest, size = await StagedBuildArtifactStorage(job_id).write_output(
            job.app_id,
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


@router.post("/jobs/{job_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
async def progress(
    job_id: UUID,
    db: Db,
    _capability: Capability,
) -> Response:
    job = await db.get(SolutionBuildJob, job_id)
    if job is None or job.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job is not running",
        )
    job.last_progress_at = datetime.now(timezone.utc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/jobs/{job_id}/cancelled")
async def cancelled(job_id: UUID, _capability: Capability) -> dict[str, bool]:
    redis = await get_redis_client()._get_redis()
    return {"cancelled": bool(await redis.exists(cancel_key(job_id)))}


@router.post("/jobs/{job_id}/status", status_code=status.HTTP_204_NO_CONTENT)
async def update_status(
    job_id: UUID,
    body: BuildJobStatusUpdate,
    db: Db,
    _capability: Capability,
) -> Response:
    job = await db.get(SolutionBuildJob, job_id)
    if job is None or job.status != "running":
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
        if not manifest or job.app_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A successful build requires an output manifest",
            )
        try:
            await StagedBuildArtifactStorage(job_id).verify_manifest(job.app_id, manifest)
        except (ValueError, BuildArtifactIntegrityError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    settings = get_settings()
    encoded_log = (body.log_excerpt or "").encode("utf-8")[
        : settings.builder_log_limit_bytes
    ]
    job.status = body.status
    job.error = body.error
    job.log_excerpt = encoded_log.decode("utf-8", errors="ignore") or None
    job.output_manifest = manifest
    job.last_progress_at = datetime.now(timezone.utc)
    job.completed_at = datetime.now(timezone.utc)

    await db.commit()
    from src.services.platform_jobs import finish_deferred_platform_job

    result = {
        "build_job_id": str(job_id),
        "output_manifest": manifest,
        "log_excerpt": job.log_excerpt,
    }
    await finish_deferred_platform_job(
        job_id,
        status=(
            "succeeded"
            if body.status == "succeeded"
            else "cancelled"
            if body.status == "cancelled"
            else "failed"
        ),
        result=result,
        error_message=body.error if body.status not in {"succeeded", "cancelled"} else None,
    )

    redis = await get_redis_client()._get_redis()
    await redis.publish(
        f"bifrost:build_job:{job_id}",
        json.dumps({"job_id": str(job_id), "status": body.status}),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
