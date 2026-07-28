"""Isolated child-process runner for one claimed platform job."""

from __future__ import annotations

import asyncio
import logging
import sys
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from src.core.database import close_db, get_db_context, init_db
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobFailure,
)
from src.jobs.platform.registry import get_platform_job_definition
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import finish_platform_job

logger = logging.getLogger(__name__)


async def run_claimed_platform_job(job_id: UUID, lease_token: UUID) -> bool:
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(PlatformJob).where(
                    PlatformJob.id == job_id,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.status.in_(("running", "cancel_requested")),
                )
            )
        ).scalar_one_or_none()
        if job is None or job.status == "cancel_requested":
            return False
        definition = get_platform_job_definition(job.job_type)
        if definition is None:
            await finish_platform_job(
                job_id,
                lease_token,
                status="failed",
                error_code="unknown_job_type",
                error_message=f"No handler is registered for {job.job_type}.",
            )
            return False
        if job.payload_version != definition.payload_version:
            await finish_platform_job(
                job_id,
                lease_token,
                status="failed",
                error_code="unsupported_payload_version",
                error_message=(
                    f"{job.job_type} payload version {job.payload_version} "
                    f"is not supported."
                ),
            )
            return False
        payload_data = job.payload
        context = PlatformJobContext(
            job_id=job.id,
            lease_token=lease_token,
            organization_id=job.organization_id,
            requested_by_user_id=job.requested_by_user_id,
            requested_by_email=job.requested_by_email,
            requested_by_name=job.requested_by_name,
        )

    try:
        payload = definition.payload_model.model_validate(payload_data)
        result = await definition.handler(context, payload)
        return await finish_platform_job(
            job_id,
            lease_token,
            status="succeeded",
            result=result or {},
        )
    except PlatformJobCancelled:
        return await finish_platform_job(
            job_id,
            lease_token,
            status="cancelled",
        )
    except PlatformJobFailure as exc:
        return await finish_platform_job(
            job_id,
            lease_token,
            status="failed",
            error_code=exc.code,
            error_message=exc.message,
            error_retryable=exc.retryable,
        )
    except ValidationError as exc:
        return await finish_platform_job(
            job_id,
            lease_token,
            status="failed",
            error_code="invalid_payload",
            error_message=str(exc),
        )
    except Exception:
        logger.exception("Unhandled platform-job handler failure", extra={"job_id": str(job_id)})
        return await finish_platform_job(
            job_id,
            lease_token,
            status="failed",
            error_code="handler_error",
            error_message="Platform job failed unexpectedly; see server logs.",
            error_retryable=False,
        )


async def _main(job_id: str, lease_token: str) -> int:
    await init_db()
    try:
        completed = await run_claimed_platform_job(
            UUID(job_id),
            UUID(lease_token),
        )
        return 0 if completed else 1
    finally:
        await close_db()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: platform.runner JOB_ID LEASE_TOKEN")
    raise SystemExit(asyncio.run(_main(sys.argv[1], sys.argv[2])))
