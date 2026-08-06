"""
Jobs API Router.

Provides HTTP endpoints for polling job status/results.
Primary use case: E2E tests and fallback when WebSockets unavailable.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.core.log_safety import log_safe
from src.core.database import get_db
from src.models.orm.platform_jobs import PlatformJob
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


class JobStatusResponse(BaseModel):
    """Response for job status query."""

    status: str = Field(
        description="Job status: 'pending', 'running', 'completed', 'failed', etc."
    )
    message: str | None = Field(default=None, description="Status message")
    # Additional fields from completion (when available)
    pulled: int = Field(default=0, description="Number of files pulled (git sync)")
    pushed: int = Field(default=0, description="Number of files pushed (git sync)")
    commit_sha: str | None = Field(default=None, description="Commit SHA if created")
    error: str | None = Field(default=None, description="Error message if failed")
    # Result data from the completed operation
    data: dict[str, Any] | None = Field(default=None, description="Operation result data")
    # Preview data for sync preview jobs
    preview: dict[str, Any] | None = Field(default=None, description="Sync preview data")
    # Conflict data for sync operations
    conflicts: list[dict[str, Any]] | None = Field(
        default=None, description="Merge conflicts if any (git sync)"
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Get job status",
    description="Poll for job completion status. Returns 'pending' if job not yet complete.",
)
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    """
    Get the status of a job by ID.

    Jobs are stored in Redis when they complete (5-minute TTL).
    Returns 'pending' if job hasn't completed or result expired.

    Used primarily for:
    - E2E tests that can't use WebSockets
    - Fallback when WebSocket connection fails
    """
    try:
        platform_job_id = UUID(job_id)
    except ValueError:
        platform_job_id = None
    if platform_job_id is not None:
        platform_job = await db.get(PlatformJob, platform_job_id)
        if platform_job is not None:
            status_map = {
                "queued": "pending",
                "running": "running",
                "cancel_requested": "running",
                "succeeded": "success",
                "failed": "failed",
                "cancelled": "cancelled",
            }
            result = platform_job.result or {}
            return JobStatusResponse(
                status=status_map[platform_job.status],
                message=platform_job.phase,
                pulled=result.get("pulled", 0),
                pushed=result.get("pushed", 0),
                commit_sha=result.get("commit_sha"),
                error=platform_job.error_message,
                data=result,
                preview=result.get("preview"),
                conflicts=result.get("conflicts"),
            )

    from src.core.redis_client import get_redis_client

    try:
        redis_client = get_redis_client()
        if redis_client:
            result_key = f"bifrost:job:{job_id}"
            data = await redis_client.get(result_key)
            logger.debug(f"Job {log_safe(job_id)} Redis data: {log_safe(data[:100]) if data else 'None'}...")
            if data:
                result = json.loads(data)
                logger.info(f"Job {log_safe(job_id)} found with status: {log_safe(result.get('status'))}")
                return JobStatusResponse(
                    status=result.get("status", "unknown"),
                    message=result.get("message"),
                    pulled=result.get("pulled", 0),
                    pushed=result.get("pushed", 0),
                    commit_sha=result.get("commit_sha"),
                    error=result.get("error"),
                    data=result.get("data"),
                    preview=result.get("preview"),
                    conflicts=result.get("conflicts"),
                )
        else:
            logger.warning(f"Redis client is None for job {log_safe(job_id)}")
    except Exception as e:
        logger.warning(f"Error fetching job status from Redis: {e}")

    # Job not found or not yet complete — check for progress phase
    phase_message: str | None = None
    try:
        redis_client = get_redis_client()
        if redis_client:
            progress_key = f"bifrost:job:{job_id}:progress"
            progress_data = await redis_client.get(progress_key)
            if progress_data:
                progress = json.loads(progress_data)
                phase_message = progress.get("phase")
    except Exception as e:
        # Best-effort progress lookup; phase_message stays None and we return "pending"
        logger.debug(f"failed to fetch progress phase for job {log_safe(job_id)}: {log_safe(e)}")

    return JobStatusResponse(status="pending", message=phase_message)
