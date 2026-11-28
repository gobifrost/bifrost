"""Durable handler that moves aged audit events into verified object-storage archives."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.audit_archive_compute import (
    AUDIT_ARCHIVE_OPERATION,
    AuditArchiveComputeError,
    decode_audit_archive_results,
    encode_audit_archive_inputs,
)
from shared.file_compute import decode_file_result, encode_file_transfer
from src.config import get_settings
from src.core.database import get_db_context
from src.core.principal import UserPrincipal
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobFailure,
    define_platform_job,
)
from src.jobs.platform.system_maintenance import enqueue_system_maintenance
from src.models.orm import AuditArchiveSegment
from src.models.orm.platform_jobs import PlatformJob
from src.scheduler.registry import ScheduledTaskOutcome
from src.services.audit_retention.archiver import (
    DeleteMismatch,
    archive_segment,
    expire_segments,
    plan_archive,
    select_batch,
)
from src.services.audit_retention.export import EXPORT_TTL_DAYS, cleanup_expired_exports
from src.services.audit_retention.format import ArchiveVerifyError
from src.services.audit_retention.settings import AuditRetentionSettingsService
from src.services.audit_retention.store import ArchiveStorageUnavailable, AuditArchiveStore
from src.services.platform_job_lease import LeaseLost
from src.services.platform_jobs import (
    enqueue_platform_job,
    ensure_platform_job_notification,
    publish_platform_job_update,
)


class AuditArchivePayload(BaseModel):
    dry_run: bool = False


async def run_audit_archive(context: PlatformJobContext, payload: AuditArchivePayload) -> dict:
    try:
        store = AuditArchiveStore(get_settings())
    except ArchiveStorageUnavailable as exc:
        raise PlatformJobFailure(
            "archive_storage_unavailable",
            "Object storage is not configured; nothing was archived or deleted.",
        ) from exc

    # The windows are frozen on first start so a resumed attempt archives
    # exactly what the original attempt planned, whatever the settings say now.
    state = dict(context.checkpoint or {})
    if "cutoff" not in state:
        async with get_db_context() as db:
            settings = await AuditRetentionSettingsService(db).get_settings()
        now = datetime.now(timezone.utc)
        state = {
            "dry_run": payload.dry_run,
            "cutoff": (now - timedelta(days=settings.hot_days)).isoformat(),
            "expiry": (
                (now - timedelta(days=settings.archive_days)).isoformat()
                if settings.archive_days is not None
                else None
            ),
            "archived_rows": 0,
            "archived_segments": 0,
            "expired_segments": 0,
            "expired_rows": 0,
        }
    cutoff = datetime.fromisoformat(state["cutoff"])
    expiry = datetime.fromisoformat(state["expiry"]) if state["expiry"] else None

    if payload.dry_run:
        async with get_db_context() as db:
            return {"dry_run": True, **await plan_archive(db, cutoff=cutoff, expiry=expiry)}

    await _recover_pending_segment(context, state)
    await context.save_checkpoint(state, phase="Archiving")
    while True:
        async with get_db_context() as db:
            rows = await select_batch(db, cutoff=cutoff)
        if not rows:
            break
        pending = {**state, "pending": {"rows": len(rows)}}
        await context.save_checkpoint(pending, phase="Compressing")
        if context.file_compute is None:
            raise PlatformJobFailure(
                "archive_compute_unavailable",
                "No eligible remote audit archive compressor is available; nothing was deleted.",
                retryable=True,
                result=pending,
            )
        try:
            inputs = await encode_file_transfer(encode_audit_archive_inputs, rows)
            try:
                outcome = await context.file_compute(AUDIT_ARCHIVE_OPERATION, inputs)
                segments = await decode_file_result(
                    outcome,
                    decode_audit_archive_results,
                    expected_rows=rows,
                )
            finally:
                inputs.close()
        except AuditArchiveComputeError as exc:
            raise PlatformJobFailure(
                "archive_compute_invalid_result",
                f"Remote archive compression could not be verified: {exc}. Nothing was deleted.",
                retryable=True,
                result=pending,
            ) from exc
        for segment in segments:
            pending = {**state, "pending": {"key": segment.key, "rows": len(segment.rows)}}
            await context.save_checkpoint(pending, phase="Archiving")
            try:
                await archive_segment(
                    store,
                    segment,
                    job_id=context.job_id,
                    lease_token=context.lease_token,
                    authorize_effect=context.authorize_effect,
                )
            except ArchiveVerifyError as exc:
                raise PlatformJobFailure(
                    "archive_verify_failed",
                    f"{segment.key}: {exc}. Nothing was deleted.",
                    retryable=True,
                    result=pending,
                ) from exc
            except DeleteMismatch as exc:
                raise PlatformJobFailure(
                    "archive_delete_mismatch",
                    f"{exc}. The delete was rolled back.",
                    retryable=True,
                    result=pending,
                ) from exc
            except LeaseLost as exc:
                raise PlatformJobCancelled from exc
            state["archived_rows"] += len(segment.rows)
            state["archived_segments"] += 1
        await context.report(
            f"Archived {state['archived_rows']} events", current=state["archived_rows"]
        )
    state.pop("pending", None)

    if expiry is not None:
        await context.save_checkpoint(state, phase="Expiring archives")
        try:
            segments, rows_expired = await expire_segments(
                store,
                expiry=expiry,
                job_id=context.job_id,
                lease_token=context.lease_token,
                authorize_effect=context.authorize_effect,
            )
        except LeaseLost as exc:
            raise PlatformJobCancelled from exc
        state["expired_segments"] += segments
        state["expired_rows"] += rows_expired

    state["expired_exports"] = await cleanup_expired_exports(
        store,
        older_than=datetime.now(timezone.utc) - timedelta(days=EXPORT_TTL_DAYS),
        authorize_effect=context.authorize_effect,
    )
    await context.log(
        "info",
        "audit_archive_completed",
        f"Archived {state['archived_rows']} audit events in {state['archived_segments']} segments; "
        f"expired {state['expired_segments']} segments ({state['expired_rows']} events) "
        f"and {state['expired_exports']} exports.",
    )
    return state


async def _recover_pending_segment(context: PlatformJobContext, state: dict) -> None:
    """Reconcile the narrow commit-before-checkpoint crash window once.

    The segment catalog is the durable evidence of an atomic catalog/delete
    commit.  A worker receipt is intentionally not consulted on recovery.
    """
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return
    key, rows = pending.get("key"), pending.get("rows")
    if key is not None and (not isinstance(key, str) or type(rows) is not int or rows < 1):
        raise PlatformJobFailure(
            "archive_checkpoint_invalid",
            "The pending archive checkpoint is invalid; nothing else was deleted.",
            result=state,
        )
    if isinstance(key, str):
        async with get_db_context() as db:
            cataloged_rows = await _cataloged_pending_rows(db, key, context.job_id)
        if cataloged_rows is not None:
            if cataloged_rows != rows:
                raise PlatformJobFailure(
                    "archive_checkpoint_mismatch",
                    "The pending archive checkpoint does not match its cataloged segment.",
                    result=state,
                )
            state["archived_rows"] += rows
            state["archived_segments"] += 1
    # A missing catalog row means the prior attempt never committed this
    # segment.  Drop the marker without incrementing; select_batch will retry
    # the original rows under the current fenced attempt.
    state.pop("pending", None)
    await context.save_checkpoint(state, phase="Archiving")


async def _cataloged_pending_rows(
    db: AsyncSession, key: str, job_id: UUID
) -> int | None:
    """Return only an exact job's durable segment-catalog evidence."""
    return await db.scalar(
        select(AuditArchiveSegment.row_count).where(
            AuditArchiveSegment.object_key == key,
            AuditArchiveSegment.platform_job_id == job_id,
        )
    )


AUDIT_ARCHIVE_DEFINITION = define_platform_job(
    "audit.archive", AuditArchivePayload, run_audit_archive
)


async def enqueue_automatic_audit_archive() -> ScheduledTaskOutcome:
    return await enqueue_system_maintenance(
        AUDIT_ARCHIVE_DEFINITION,
        AuditArchivePayload(),
        title="Archive audit events",
    )


async def enqueue_manual_audit_archive(
    db: AsyncSession, user: UserPrincipal, *, dry_run: bool
) -> tuple[PlatformJob, bool]:
    """Queue an administrator-requested archive run or preview (deduplicated)."""
    job, reused = await enqueue_platform_job(
        db,
        AUDIT_ARCHIVE_DEFINITION,
        AuditArchivePayload(dry_run=dry_run),
        dedupe_key="manual-dry-run" if dry_run else "manual",
        resource_lock_key=AUDIT_ARCHIVE_DEFINITION.job_type,
        priority=500,
        organization_id=None,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="system",
        resource_id=AUDIT_ARCHIVE_DEFINITION.job_type,
        title="Preview audit archiving" if dry_run else "Archive audit events",
        action_url="/settings/maintenance",
    )
    if job.notification_id is None:
        await ensure_platform_job_notification(db, job)
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    return job, reused
