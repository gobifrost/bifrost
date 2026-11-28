"""Scheduler-owned processing for durable Solution backup export jobs."""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, String, cast, or_, and_

from src.config import get_settings
from src.core.database import get_db_context
from src.models.contracts.notifications import NotificationStatus, NotificationUpdate
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution
from src.models.orm.platform_jobs import PlatformJob
from src.jobs.platform.base import PlatformJobContext, PlatformJobCancelled, PlatformJobFailure
from shared.file_compute import run_owned_file_io
from src.services.solutions.archive_compute import pack_solution_archive
from src.services.solutions.export import append_export_runtime_content
from src.services.solutions.write_lock import solution_write_lock
from src.services.notification_service import get_notification_service
from src.services.solutions.export_jobs import (
    SolutionExportArtifactService,
    delete_solution_export_artifact,
    export_attempt_artifact_storage_key,
    capture_export_source,
)

logger = logging.getLogger(__name__)

def _now() -> datetime:
    return datetime.now(timezone.utc)




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


async def _authorize(db, context: PlatformJobContext) -> None:
    if not await context.authorize_effect(db):
        raise PlatformJobCancelled


async def _notify(context: PlatformJobContext, notification_id, update: NotificationUpdate) -> None:
    if notification_id is None:
        return
    async with get_db_context() as db:
        await _authorize(db, context)
        await _update_notification(notification_id, update)


async def _fail_job(context: PlatformJobContext, job_id: UUID, message: str) -> None:
    async with get_db_context() as db:
        await _authorize(db, context)
        job = await db.get(SolutionExportJob, job_id, with_for_update=True)
        if job is None or job.status == "completed" or job.organization_id != context.organization_id:
            return
        await delete_solution_export_artifact(db, job.artifact_storage_key)
        job.artifact_storage_key = None
        job.status = "failed"
        job.progress_percent = 100
        job.message = "Backup failed"
        job.failure_message = message
        job.encrypted_options = None
        job.completed_at = _now()
        notification_id = job.notification_id
    await _notify(context, notification_id, NotificationUpdate(
        status=NotificationStatus.FAILED, description="Backup failed", percent=100, error=message,
    ))


async def run_solution_export_job(job_id: UUID, *, context: PlatformJobContext) -> bool:
    """Capture/crypto/publication stay API-owned; only source compute is remote."""
    if context.file_compute is None:
        raise PlatformJobFailure("compute_unavailable", "Solution export requires its worker compute stage.")
    try:
        async with get_db_context() as db:
            await _authorize(db, context)
            job = await db.get(SolutionExportJob, job_id, with_for_update=True)
            if job is None:
                raise PlatformJobFailure("export_job_missing", "Export job is missing.")
            if job.organization_id != context.organization_id:
                raise PlatformJobFailure("export_scope_changed", "Export scope changed.")
            if job.status == "completed" and job.artifact_storage_key:
                await _update_notification(job.notification_id, NotificationUpdate(
                    status=NotificationStatus.COMPLETED, description="Backup ready", percent=100,
                    result={"job_id": str(job_id)},
                ))
                return True
            if job.status not in {"pending", "running"}:
                return False
            if not job.encrypted_options:
                raise PlatformJobFailure("export_options_missing", "Missing backup export options")
            try:
                options = SolutionExportArtifactService.decrypt_options(job.encrypted_options)
            except Exception:
                raise PlatformJobFailure("export_options_invalid", "Invalid backup export options") from None
            encrypted_options = job.encrypted_options
            solution_id = job.solution_id
            # A successor can remove the prior attempt's unpublished upload.
            await delete_solution_export_artifact(db, job.artifact_storage_key)
            storage_key = export_attempt_artifact_storage_key(solution_id, job_id, uuid4())
            job.artifact_storage_key = storage_key
            job.status, job.progress_percent, job.message = "running", 5, "Building backup"
            job.claimed_at, job.failure_message = _now(), None
            job.expires_at = _now() + timedelta(days=get_settings().solution_export_retention_days)
            notification_id = job.notification_id
        await _notify(context, notification_id, NotificationUpdate(
            status=NotificationStatus.RUNNING, description="Building backup", percent=5,
        ))
        async with solution_write_lock(solution_id):
            with tempfile.TemporaryDirectory(prefix="bifrost-export-attempt-") as temporary:
                source_path = Path(temporary) / "source.zip"
                async with get_db_context() as db:
                    solution = await db.get(Solution, solution_id)
                    if solution is None or solution.organization_id != context.organization_id:
                        raise PlatformJobFailure("solution_not_found", "Solution export target is missing or changed.")
                    bundle = await capture_export_source(db, solution, options, source_path)
                # No session or owner/job lock spans physical computation.
                packed = await pack_solution_archive(source_path, context.file_compute)
                await run_owned_file_io(source_path.write_bytes, packed)
                artifact_path = Path(temporary) / "backup.zip"
                async with get_db_context() as db:
                    await _authorize(db, context)
                    await db.commit()  # crypto/local temporary I/O holds no durable locks
                    await append_export_runtime_content(
                        source_path, bundle, db, artifact_path, password=options.password or "",
                    )
                async with get_db_context() as db:
                    await _authorize(db, context)
                    job = await db.get(SolutionExportJob, job_id, with_for_update=True)
                    solution = await db.get(Solution, solution_id)
                    if (job is None or solution is None or job.solution_id != solution_id
                            or solution.organization_id != context.organization_id
                            or job.encrypted_options != encrypted_options
                            or job.artifact_storage_key != storage_key or job.status != "running"):
                        raise PlatformJobFailure("export_target_changed", "Export target changed during preparation.")
                    digest, size = await SolutionExportArtifactService(db).upload_artifact(storage_key, artifact_path)
                    await _authorize(db, context)
                    job.artifact_sha256, job.artifact_size_bytes = digest, size
                    job.artifact_filename = job.artifact_filename or SolutionExportArtifactService.artifact_filename(solution)
                    job.status, job.progress_percent, job.message = "completed", 100, "Backup ready"
                    job.completed_at, job.encrypted_options = _now(), None
        await _notify(context, notification_id, NotificationUpdate(
            status=NotificationStatus.COMPLETED, description="Backup ready", percent=100,
            result={"job_id": str(job_id)},
        ))
        return True
    except PlatformJobCancelled:
        # Keep encrypted input and private attempt key for canonical recovery.
        raise
    except PlatformJobFailure as exc:
        await _fail_job(context, job_id, exc.message if exc.code.startswith("export_options_") else "Backup export failed")
        raise
    except Exception as exc:
        logger.warning("Solution export failed (%s)", type(exc).__name__)
        await _fail_job(context, job_id, "Backup export failed")
        return False


async def cleanup_expired_solution_export_jobs(batch_limit: int | None = None) -> int:
    """Expire old solution export job artifacts while preserving job history."""
    settings = get_settings()
    limit = batch_limit or settings.solution_export_cleanup_batch_size
    now = _now()

    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(SolutionExportJob)
                .outerjoin(PlatformJob, and_(
                    PlatformJob.job_type == "solution.export",
                    PlatformJob.resource_type == "solution_export",
                    PlatformJob.resource_id == cast(SolutionExportJob.id, String),
                ))
                .where(SolutionExportJob.expires_at <= now)
                .where(or_(
                    SolutionExportJob.status == "completed",
                    SolutionExportJob.artifact_storage_key.is_not(None),
                    SolutionExportJob.encrypted_options.is_not(None),
                ))
                .where(or_(
                    SolutionExportJob.status.in_(("completed", "failed", "expired")),
                    and_(SolutionExportJob.status.in_(("pending", "running")),
                         PlatformJob.status.in_(("failed", "cancelled"))),
                ))
                .order_by(SolutionExportJob.expires_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True, of=SolutionExportJob)
            )
        ).scalars().all()

        cleaned = 0
        for row in rows:
            await delete_solution_export_artifact(db, row.artifact_storage_key)
            if row.status == "completed":
                row.status = "expired"
                row.message = "Backup expired"
            elif row.status in {"pending", "running"}:
                row.status = "failed"
                row.message = "Backup failed"
            row.artifact_storage_key = None
            row.encrypted_options = None
            cleaned += 1

        await db.commit()

    logger.info("Expired solution export jobs cleaned", extra={"cleaned": cleaned})
    return cleaned
