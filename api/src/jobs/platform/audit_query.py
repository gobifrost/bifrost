"""Durable handler that exports archived and current audit events to one file."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.principal import UserPrincipal
from src.jobs.platform.audit_archive import AUDIT_ARCHIVE_DEFINITION
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.contracts.audit_retention import AuditExportRequest
from src.models.orm.platform_jobs import PlatformJob
from src.services.audit_retention.export import AuditQueryPayload, ReachSnapshot, build_export
from src.services.audit_retention.store import ArchiveStorageUnavailable, AuditArchiveStore
from src.services.authorization.reach import OrgReach
from src.services.platform_jobs import (
    enqueue_platform_job,
    ensure_platform_job_notification,
    publish_platform_job_update,
)


async def run_audit_query(context: PlatformJobContext, payload: AuditQueryPayload) -> dict:
    try:
        store = AuditArchiveStore(get_settings())
    except ArchiveStorageUnavailable as exc:
        raise PlatformJobFailure(
            "archive_storage_unavailable",
            "Object storage is not configured; nothing was exported.",
        ) from exc
    return await build_export(context, payload, store)


AUDIT_QUERY_DEFINITION = PlatformJobDefinition(
    job_type="audit.query",
    payload_version=1,
    payload_model=AuditQueryPayload,
    handler=run_audit_query,
    policy=PlatformJobPolicy(
        timeout_seconds=2 * 60 * 60,
        max_attempts=2,
        # A rerun rewrites the same export key from the start.
        retry_on_runner_loss=True,
        min_memory_headroom_mb=256,
        allow_running_cancellation=True,
    ),
)


async def enqueue_audit_export(
    db: AsyncSession, user: UserPrincipal, request: AuditExportRequest, reach: OrgReach
) -> PlatformJob:
    """Queue one export of ``request`` within ``reach``; every export is its own job."""
    job, _ = await enqueue_platform_job(
        db,
        AUDIT_QUERY_DEFINITION,
        AuditQueryPayload(request=request, reach=ReachSnapshot.of(reach)),
        dedupe_key=None,
        # Exports never overlap an archive run, so no event is in two places mid-export.
        resource_lock_key=AUDIT_ARCHIVE_DEFINITION.job_type,
        organization_id=None,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="audit_export",
        resource_id=None,
        title="Export audit events",
        action_url="/audit",
    )
    await ensure_platform_job_notification(db, job)
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    return job
