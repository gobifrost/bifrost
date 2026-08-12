"""Durable state, deduplication, and observation for platform jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.core.pubsub import manager as pubsub_manager
from src.jobs.platform.base import PlatformJobDefinition
from src.models.contracts.notifications import (
    NotificationCategory,
    NotificationCreate,
    NotificationStatus,
    NotificationUpdate,
)
from src.models.contracts.platform_jobs import (
    PlatformJobError,
    PlatformJobProgress,
    PlatformJobPublic,
    PlatformJobStatus,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.notification_service import get_notification_service

logger = logging.getLogger(__name__)
ACTIVE_PLATFORM_JOB_STATUSES = ("queued", "running", "waiting", "cancel_requested")
TERMINAL_PLATFORM_JOB_STATUSES = ("succeeded", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def platform_job_to_public(job: PlatformJob) -> PlatformJobPublic:
    from src.jobs.platform.registry import get_platform_job_definition

    error = None
    if job.error_message:
        error = PlatformJobError(
            code=job.error_code or "job_failed",
            message=job.error_message,
            retryable=bool(job.error_retryable),
        )
    definition = get_platform_job_definition(job.job_type)
    can_cancel = job.status in ("queued", "waiting") or (
        job.status == "running"
        and definition is not None
        and definition.policy.allow_running_cancellation
    )
    return PlatformJobPublic(
        id=job.id,
        job_type=job.job_type,
        payload_version=job.payload_version,
        organization_id=job.organization_id,
        resource_type=job.resource_type,
        resource_id=job.resource_id,
        resource_lock_key=job.resource_lock_key,
        priority=job.priority,
        title=job.title,
        action_url=job.action_url,
        requested_by_user_id=job.requested_by_user_id,
        requested_by_name=job.requested_by_name,
        status=PlatformJobStatus(job.status),
        progress=PlatformJobProgress(
            phase=job.phase,
            current=job.progress_current,
            total=job.progress_total,
            percent=job.progress_percent,
        ),
        revision=job.revision,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        can_cancel=can_cancel,
        result=job.result,
        error=error,
        notification_id=job.notification_id,
        memory_start_bytes=job.memory_start_bytes,
        memory_peak_bytes=job.memory_peak_bytes,
        memory_limit_bytes=job.memory_limit_bytes,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _notification_status(status: str) -> NotificationStatus:
    return {
        "queued": NotificationStatus.PENDING,
        "running": NotificationStatus.RUNNING,
        "waiting": NotificationStatus.RUNNING,
        "cancel_requested": NotificationStatus.RUNNING,
        "succeeded": NotificationStatus.COMPLETED,
        "failed": NotificationStatus.FAILED,
        "cancelled": NotificationStatus.CANCELLED,
    }[status]


async def publish_platform_job_update(job: PlatformJob) -> None:
    """Broadcast the exact HTTP contract and update the notification projection."""
    public = platform_job_to_public(job)
    try:
        await pubsub_manager.broadcast(
            f"notification:{job.requested_by_user_id}",
            {
                "type": "platform_job_updated",
                "job": public.model_dump(mode="json"),
            },
        )
    except Exception:
        logger.warning(
            "Failed to broadcast platform job update",
            extra={"platform_job_id": str(job.id)},
            exc_info=True,
        )

    if job.notification_id is None:
        return
    try:
        description = job.phase or job.status.replace("_", " ").capitalize()
        await get_notification_service().update_notification(
            str(job.notification_id),
            NotificationUpdate(
                status=_notification_status(job.status),
                description=description[:500],
                percent=job.progress_percent,
                error=job.error_message[:1000] if job.error_message else None,
                result=(
                    {"job_id": str(job.id), **(job.result or {})}
                    if job.status == "succeeded"
                    else None
                ),
            ),
        )
    except Exception:
        logger.warning(
            "Failed to update platform job notification projection",
            extra={"platform_job_id": str(job.id)},
            exc_info=True,
        )


async def enqueue_platform_job(
    db: AsyncSession,
    definition: PlatformJobDefinition,
    payload: BaseModel | dict[str, Any],
    *,
    dedupe_key: str | None,
    resource_lock_key: str | None = None,
    priority: int = 100,
    organization_id: UUID | None,
    requested_by_user_id: UUID | str,
    requested_by_email: str,
    requested_by_name: str,
    resource_type: str | None,
    resource_id: str | None,
    title: str,
    action_url: str | None,
    job_id: UUID | None = None,
) -> tuple[PlatformJob, bool]:
    """Create or reuse one active job under a durable deduplication key."""
    parsed_payload = definition.payload_model.model_validate(payload)
    if dedupe_key is not None:
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:platform-job:' || :lock_key))"
            ),
            {"lock_key": f"{definition.job_type}:{dedupe_key}"},
        )
        existing = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.job_type == definition.job_type,
                    PlatformJob.dedupe_key == dedupe_key,
                    PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES),
                )
                .order_by(PlatformJob.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, True

    payload_json = parsed_payload.model_dump(mode="json")
    encrypted_payload = None
    if definition.encrypt_payload:
        from src.core.security import encrypt_secret

        encrypted_payload = encrypt_secret(parsed_payload.model_dump_json())
        payload_json = {"protected": True}

    job = PlatformJob(
        id=job_id or uuid4(),
        job_type=definition.job_type,
        payload_version=definition.payload_version,
        payload=payload_json,
        encrypted_payload=encrypted_payload,
        dedupe_key=dedupe_key,
        resource_lock_key=resource_lock_key,
        priority=priority,
        organization_id=organization_id,
        requested_by_user_id=str(requested_by_user_id),
        requested_by_email=requested_by_email,
        requested_by_name=requested_by_name,
        resource_type=resource_type,
        resource_id=resource_id,
        title=title[:200],
        action_url=action_url,
        status="queued",
        phase="Queued",
        progress_percent=0,
        max_attempts=definition.policy.max_attempts,
        timeout_seconds=definition.policy.timeout_seconds,
        retry_on_runner_loss=definition.policy.retry_on_runner_loss,
    )
    db.add(job)
    await db.flush()
    return job, False


async def ensure_platform_job_notification(
    db: AsyncSession,
    job: PlatformJob,
) -> UUID:
    """Attach one existing-UI notification projection to a durable job."""
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtext('bifrost:platform-job-notification:' || :job_id))"
        ),
        {"job_id": str(job.id)},
    )
    await db.refresh(job)
    if job.notification_id is not None:
        return job.notification_id

    notification = await get_notification_service().create_notification(
        user_id=job.requested_by_user_id,
        request=NotificationCreate(
            category=NotificationCategory.SYSTEM,
            title=job.title,
            description=job.phase,
            percent=job.progress_percent,
            metadata={
                "job_id": str(job.id),
                "job_type": job.job_type,
                "resource_type": job.resource_type,
                "resource_id": job.resource_id,
                "action_url": job.action_url,
            },
        ),
        initial_status=NotificationStatus.PENDING,
    )
    notification_id = UUID(notification.id)
    job.notification_id = notification_id
    await db.flush()
    return notification_id


async def update_platform_job_progress(
    job_id: UUID,
    lease_token: UUID,
    *,
    phase: str,
    current: int,
    total: int | None,
    percent: float | None,
) -> bool:
    """Persist progress only for the current fenced runner attempt."""
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.id == job_id,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.status.in_(("running", "cancel_requested")),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.status == "cancel_requested":
            return False
        job.phase = phase[:200]
        job.progress_current = max(0, current)
        job.progress_total = total
        job.progress_percent = percent
        job.revision += 1
        await db.commit()
    await publish_platform_job_update(job)
    return True


async def finish_platform_job(
    job_id: UUID,
    lease_token: UUID,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    error_retryable: bool = False,
) -> bool:
    """Finalize only the currently leased attempt; stale runners are fenced out."""
    if status not in TERMINAL_PLATFORM_JOB_STATUSES:
        raise ValueError(f"Invalid terminal platform-job status: {status}")
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.id == job_id,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.status.in_(("running", "cancel_requested")),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        job.status = status
        job.phase = {
            "succeeded": "Completed",
            "failed": "Failed",
            "cancelled": "Cancelled",
        }[status]
        if status == "succeeded":
            job.progress_percent = 100
        job.result = result
        job.error_code = error_code
        job.error_message = error_message.strip()[:4000] if error_message else None
        job.error_retryable = error_retryable if error_message else None
        job.completed_at = _now()
        job.lease_owner = None
        job.lease_token = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.revision += 1
        await db.commit()
    await publish_platform_job_update(job)
    return True


async def defer_platform_job(
    job_id: UUID,
    lease_token: UUID,
    *,
    phase: str,
    result: dict[str, Any] | None = None,
) -> bool:
    """Release a runner after durable child work has been dispatched."""
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.id == job_id,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.status == "running",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        job.status = "waiting"
        job.phase = phase[:200]
        job.result = result
        job.lease_owner = None
        job.lease_token = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.revision += 1
        await db.commit()
    await publish_platform_job_update(job)
    return True


async def finish_deferred_platform_job(
    job_id: UUID,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> bool:
    """Complete an externally-tracked job that no longer holds a runner lease."""
    if status not in TERMINAL_PLATFORM_JOB_STATUSES:
        raise ValueError(f"Invalid terminal platform-job status: {status}")
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(PlatformJob)
                .where(PlatformJob.id == job_id, PlatformJob.status == "waiting")
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        job.status = status
        job.phase = {"succeeded": "Completed", "failed": "Failed", "cancelled": "Cancelled"}[status]
        job.progress_percent = 100 if status == "succeeded" else job.progress_percent
        job.result = result
        job.error_code = "child_work_failed" if error_message else None
        job.error_message = error_message[:4000] if error_message else None
        job.completed_at = _now()
        job.revision += 1
        await db.commit()
    await publish_platform_job_update(job)
    return True


async def update_deferred_platform_job_progress(
    job_id: UUID,
    *,
    phase: str,
    current: int,
    total: int,
) -> bool:
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(PlatformJob)
                .where(PlatformJob.id == job_id, PlatformJob.status == "waiting")
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        job.phase = phase[:200]
        job.progress_current = current
        job.progress_total = total
        job.progress_percent = 100 * current / total if total else 100
        job.revision += 1
        await db.commit()
    await publish_platform_job_update(job)
    return True


async def request_platform_job_cancel(
    db: AsyncSession,
    job: PlatformJob,
) -> tuple[PlatformJob, bool]:
    if job.status in TERMINAL_PLATFORM_JOB_STATUSES:
        return job, False
    if job.status in ("running", "cancel_requested"):
        from src.jobs.platform.registry import get_platform_job_definition

        definition = get_platform_job_definition(job.job_type)
        if definition is None or not definition.policy.allow_running_cancellation:
            return job, False
    now = _now()
    accepted = job.cancel_requested_at is None
    job.cancel_requested_at = job.cancel_requested_at or now
    if job.status in ("queued", "waiting"):
        job.status = "cancelled"
        job.phase = "Cancelled"
        job.completed_at = now
    else:
        job.status = "cancel_requested"
        job.phase = "Cancellation requested"
    if accepted:
        job.revision += 1
    await db.commit()
    await publish_platform_job_update(job)
    return job, accepted
