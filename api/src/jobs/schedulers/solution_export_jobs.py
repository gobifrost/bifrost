"""Scheduler-owned processing for durable Solution backup export jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

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


async def _fail_job(job_id: UUID, message: str) -> None:
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


async def run_solution_export_job(job_id: UUID) -> bool:
    """Build one export projection; platform-job fencing owns retries."""
    artifact_path: Path | None = None
    uploaded_storage_key: str | None = None

    try:
        async with get_db_context() as db:
            job = await db.get(SolutionExportJob, job_id, with_for_update=True)
            if job is None or job.status not in {"pending", "running"}:
                return False
            if job.status == "pending":
                job.status = "running"
                job.progress_percent = 5
                job.claimed_at = _now()
                job.message = "Building backup"
                job.failure_message = None
                await db.commit()
                await _update_notification(
                    job.notification_id,
                    NotificationUpdate(
                        status=NotificationStatus.RUNNING,
                        description="Building backup",
                        percent=5,
                    ),
                )

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
