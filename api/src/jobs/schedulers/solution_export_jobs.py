"""Scheduler-owned processing for durable Solution backup export jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select

from src.config import get_settings
from src.core.database import get_db_context
from src.models.contracts.notifications import NotificationStatus, NotificationUpdate
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution
from src.services.notification_service import get_notification_service
from src.services.solutions.export_jobs import (
    SolutionExportArtifactService,
    delete_solution_export_artifact,
    export_artifact_storage_key,
)

logger = logging.getLogger(__name__)

DEFAULT_PROCESS_BATCH_LIMIT = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sanitize_failure_message(exc: BaseException | str) -> str:
    raw = str(exc).strip() if not isinstance(exc, str) else exc.strip()
    if not raw:
        return "Backup export failed"
    return raw.replace("\n", " ").replace("\r", " ")[:500]


def _unlink_tempfile(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed to remove temporary solution export file", exc_info=True)


async def _update_notification(
    notification_id: object | None,
    update: NotificationUpdate,
) -> None:
    if not notification_id:
        return
    try:
        await get_notification_service().update_notification(str(notification_id), update)
    except Exception:
        logger.warning(
            "Failed to update solution export notification",
            extra={"notification_id": str(notification_id)},
            exc_info=True,
        )


async def _fail_job(job_id: object, message: str) -> None:
    async with get_db_context() as db:
        job = await db.get(SolutionExportJob, job_id)
        if job is None:
            return
        job.status = "failed"
        job.progress_percent = 100
        job.message = "Backup failed"
        job.failure_message = _sanitize_failure_message(message)
        job.encrypted_options = None
        job.completed_at = _now()
        await db.commit()
        await _update_notification(
            job.notification_id,
            NotificationUpdate(
                status=NotificationStatus.FAILED,
                description="Backup failed",
                percent=100,
                error=job.failure_message,
            ),
        )


async def _reset_stale_running_jobs() -> int:
    settings = get_settings()
    cutoff = _now() - timedelta(minutes=settings.solution_export_stale_running_minutes)
    stale_message = "Backup export job was abandoned and will need to be retried"

    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(SolutionExportJob)
                .where(SolutionExportJob.status == "running")
                .where(
                    or_(
                        SolutionExportJob.claimed_at.is_(None),
                        SolutionExportJob.claimed_at <= cutoff,
                    )
                )
                .order_by(SolutionExportJob.claimed_at.asc())
            )
        ).scalars().all()
        if not rows:
            return 0

        notifications = []
        for row in rows:
            row.status = "failed"
            row.progress_percent = 100
            row.message = "Backup failed"
            row.failure_message = stale_message
            row.encrypted_options = None
            row.completed_at = _now()
            notifications.append(row.notification_id)

        await db.commit()

    for notification_id in notifications:
        await _update_notification(
            notification_id,
            NotificationUpdate(
                status=NotificationStatus.FAILED,
                description="Backup failed",
                percent=100,
                error=stale_message,
            ),
        )

    return len(rows)


async def _claim_pending_jobs(limit: int) -> list[object]:
    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(SolutionExportJob)
                .where(SolutionExportJob.status == "pending")
                .order_by(SolutionExportJob.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        if not rows:
            return []

        now = _now()
        notifications = []
        for row in rows:
            row.status = "running"
            row.progress_percent = 5
            row.claimed_at = now
            row.message = "Building backup"
            row.failure_message = None
            notifications.append(row.notification_id)

        ids = [row.id for row in rows]
        await db.commit()

    for notification_id in notifications:
        await _update_notification(
            notification_id,
            NotificationUpdate(
                status=NotificationStatus.RUNNING,
                description="Building backup",
                percent=5,
            ),
        )

    return ids


async def _process_claimed_job(job_id: object) -> bool:
    artifact_path: Path | None = None
    uploaded_storage_key: str | None = None

    try:
        async with get_db_context() as db:
            job = await db.get(SolutionExportJob, job_id)
            if job is None or job.status != "running":
                return False

            if not job.encrypted_options:
                await db.commit()
                await _fail_job(job_id, "Missing backup export options")
                return False

            service = SolutionExportArtifactService(db)
            try:
                options = service.decrypt_options(job.encrypted_options)
            except Exception:
                await db.commit()
                await _fail_job(job_id, "Invalid backup export options")
                return False

            solution = await db.get(Solution, job.solution_id)
            if solution is None:
                await db.commit()
                await _fail_job(job_id, "Solution not found")
                return False

            artifact_path = await service.build_zip_tempfile(solution, options)
            uploaded_storage_key = export_artifact_storage_key(job.solution_id, job.id)
            sha256, size = await service.upload_artifact(uploaded_storage_key, artifact_path)

            completed_at = _now()
            settings = get_settings()
            job.artifact_storage_key = uploaded_storage_key
            job.artifact_filename = job.artifact_filename or service.artifact_filename(solution)
            job.artifact_size_bytes = size
            job.artifact_sha256 = sha256
            job.expires_at = completed_at + timedelta(
                days=settings.solution_export_retention_days
            )
            job.completed_at = completed_at
            job.status = "completed"
            job.progress_percent = 100
            job.message = "Backup ready"
            job.failure_message = None
            job.encrypted_options = None
            await db.commit()
            await _update_notification(
                job.notification_id,
                NotificationUpdate(
                    status=NotificationStatus.COMPLETED,
                    description="Backup ready",
                    percent=100,
                    result={"job_id": str(job.id)},
                ),
            )
            return True
    except Exception as exc:
        failure_message = _sanitize_failure_message(exc)
        logger.exception(
            "Solution export job failed",
            extra={"solution_export_job_id": str(job_id)},
        )
        if uploaded_storage_key:
            async with get_db_context() as db:
                await delete_solution_export_artifact(db, uploaded_storage_key)
        await _fail_job(job_id, failure_message)
        return False
    finally:
        _unlink_tempfile(artifact_path)


async def process_solution_export_jobs(
    batch_limit: int | None = None,
) -> tuple[int, int]:
    """Process pending durable Solution backup exports in the scheduler process."""
    limit = batch_limit or DEFAULT_PROCESS_BATCH_LIMIT
    stale_failed = await _reset_stale_running_jobs()
    claimed_ids = await _claim_pending_jobs(limit)

    processed = stale_failed
    failed = stale_failed
    for job_id in claimed_ids:
        processed += 1
        if not await _process_claimed_job(job_id):
            failed += 1

    logger.info(
        "Solution export jobs processed",
        extra={"processed": processed, "failed": failed},
    )
    return processed, failed


async def cleanup_expired_solution_export_jobs(batch_limit: int | None = None) -> int:
    """Expire old solution export job artifacts while preserving job history."""
    settings = get_settings()
    limit = batch_limit or settings.solution_export_cleanup_batch_size
    now = _now()

    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(SolutionExportJob)
                .where(SolutionExportJob.expires_at <= now)
                .where(SolutionExportJob.status.in_(("completed", "failed", "expired")))
                .order_by(SolutionExportJob.expires_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

        cleaned = 0
        for row in rows:
            await delete_solution_export_artifact(db, row.artifact_storage_key)
            if row.status == "completed":
                row.status = "expired"
                row.message = "Backup expired"
            row.artifact_storage_key = None
            row.encrypted_options = None
            cleaned += 1

        await db.commit()

    logger.info("Expired solution export jobs cleaned", extra={"cleaned": cleaned})
    return cleaned
