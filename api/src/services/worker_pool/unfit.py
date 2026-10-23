"""Work no executor can ever hold fails visibly (#769 stage 6).

Runs are settled with their dispatches (``work_dispatch``). Here: a queued
local platform job whose memory requirement exceeds the largest executor
fails, and a service whose request exceeds it is blocked ``too_large`` (and
unblocked once an executor large enough exists). "Largest" includes what the
worker controller can launch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.services import ServiceDefinition
from src.models.orm.workflows import Workflow
from src.services.memory_requests import workflow_request_sql

logger = logging.getLogger(__name__)

TOO_LARGE = "too_large"


def too_large_message(request_bytes: int, largest_bytes: int) -> str:
    return (
        f"This work needs {request_bytes // 2**20} MiB of memory; "
        f"the largest executor can hold {largest_bytes // 2**20} MiB."
    )


async def settle_work_that_never_fits() -> None:
    from src.services.platform_jobs import publish_platform_job_update
    from src.services.work_dispatch import largest_budget

    async with get_db_context() as db:
        largest = await largest_budget(db)
        if largest is None:
            return  # No executor has reported: an outage is not too-large work.
        failed = await fail_platform_jobs_that_never_fit(db, largest)
        await block_services_that_never_fit(db, largest)
        await db.commit()
    for job in failed:
        await publish_platform_job_update(job)


async def fail_platform_jobs_that_never_fit(
    db: AsyncSession, largest: int
) -> list[PlatformJob]:
    """Fail queued local platform jobs larger than ``largest``."""
    jobs = (
        await db.scalars(
            select(PlatformJob)
            .where(
                PlatformJob.status == "queued",
                PlatformJob.execution_backend == "local",
                PlatformJob.memory_required_bytes > largest,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for job in jobs:
        logger.warning("Platform job failed: no executor can hold it", extra={"job_id": str(job.id)})
        job.status = "failed"
        job.phase = "Failed"
        job.error_code = TOO_LARGE
        job.error_message = too_large_message(job.memory_required_bytes, largest)
        job.error_retryable = False
        job.completed_at = datetime.now(timezone.utc)
        job.revision += 1
    return list(jobs)


async def block_services_that_never_fit(db: AsyncSession, largest: int) -> None:
    """Block services larger than ``largest``; unblock those that fit again.
    Only the definitions whose state changes are locked."""
    request = workflow_request_sql(Workflow)
    changing = (
        await db.execute(
            select(ServiceDefinition, request > largest)
            .join(Workflow, Workflow.id == ServiceDefinition.workflow_id)
            .where(
                (ServiceDefinition.blocked_reason.is_(None) & (request > largest))
                | ((ServiceDefinition.blocked_reason == TOO_LARGE) & (request <= largest))
            )
            .with_for_update(of=ServiceDefinition, skip_locked=True)
        )
    ).all()
    for definition, unfit in changing:
        logger.warning(
            "Service blocked: no executor can hold it"
            if unfit
            else "Service unblocked: an executor can hold it now",
            extra={"service_id": str(definition.id)},
        )
        definition.blocked_reason = TOO_LARGE if unfit else None


__all__ = [
    "TOO_LARGE",
    "block_services_that_never_fit",
    "fail_platform_jobs_that_never_fit",
    "settle_work_that_never_fits",
    "too_large_message",
]
