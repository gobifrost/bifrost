"""Move old audit events from Postgres into verified object-storage segments.

The rule this module enforces: a Postgres row is deleted only after its
segment has been uploaded, read back and verified, and only inside a
transaction that catalogs the segment while holding the job's current lease.
A runner that lost its lease therefore can never delete anything.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, literal_column, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.contracts.audit_retention import AuditRetentionInfo, AuditRetentionSettings
from src.models.orm import AuditArchiveSegment, AuditLog, Organization, PlatformJob, User
from src.services.audit_retention.format import (
    SCHEMA_VERSION,
    ArchiveRow,
    Segment,
    line_size,
    verify_segment,
)
from src.services.audit_retention.settings import AuditRetentionSettingsService
from src.services.audit_retention.store import AuditArchiveStore

BATCH_ROWS = 5000
BATCH_BYTES = 64 * 1024 * 1024


class LeaseLost(Exception):
    """The job no longer holds its lease; another runner owns the work."""


class DeleteMismatch(Exception):
    """The delete removed a different number of rows than the segment holds."""


def snapshot_query():
    """Audit events with the actor and organization names an archive line snapshots."""
    return (
        select(AuditLog, User.email, User.name, Organization.name)
        .outerjoin(User, User.id == AuditLog.user_id)
        .outerjoin(Organization, Organization.id == AuditLog.organization_id)
    )


def snapshot_row(log: AuditLog, email: str | None, name: str | None, org_name: str | None) -> ArchiveRow:
    return ArchiveRow(
        id=log.id,
        created_at=log.created_at,
        organization_id=log.organization_id,
        user_id=log.user_id,
        action=log.action,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        outcome=log.outcome,
        source=log.source,
        operation_id=log.operation_id,
        surface=log.surface,
        execution_id=log.execution_id,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        details=log.details,
        actor_email=email,
        actor_name=name,
        organization_name=org_name,
    )


async def select_batch(
    db: AsyncSession,
    *,
    cutoff: datetime,
    limit: int = BATCH_ROWS,
    max_bytes: int = BATCH_BYTES,
) -> list[ArchiveRow]:
    """Oldest events created before ``cutoff``, bounded by row count and encoded size."""
    result = await db.execute(
        snapshot_query()
        .where(AuditLog.created_at < cutoff)
        .order_by(AuditLog.created_at, AuditLog.id)
        .limit(limit)
    )
    rows: list[ArchiveRow] = []
    total = 0
    for log, email, name, org_name in result.all():
        row = snapshot_row(log, email, name, org_name)
        size = line_size(row)
        if rows and total + size > max_bytes:
            break
        rows.append(row)
        total += size
    return rows


async def _hold_lease(db: AsyncSession, job_id: UUID, lease_token: UUID) -> None:
    # Holding the job row lock fences lease recovery until this transaction ends.
    held = await db.execute(
        select(PlatformJob.id)
        .where(
            PlatformJob.id == job_id,
            PlatformJob.lease_token == lease_token,
            PlatformJob.status == "running",
        )
        .with_for_update()
    )
    if held.scalar_one_or_none() is None:
        raise LeaseLost(str(job_id))


async def commit_segment(
    db: AsyncSession,
    segment: Segment,
    *,
    job_id: UUID,
    lease_token: UUID,
) -> None:
    """Catalog a verified segment and delete its rows, under the job's lease."""
    await _hold_lease(db, job_id, lease_token)
    first, last = segment.rows[0], segment.rows[-1]
    db.add(
        AuditArchiveSegment(
            organization_id=segment.organization_id,
            day=segment.day,
            object_key=segment.key,
            schema_version=SCHEMA_VERSION,
            row_count=len(segment.rows),
            byte_size=len(segment.body),
            sha256=segment.sha256,
            first_created_at=first.created_at,
            last_created_at=last.created_at,
            first_id=first.id,
            last_id=last.id,
            platform_job_id=job_id,
        )
    )
    await db.flush()
    deleted = await db.execute(delete(AuditLog).where(AuditLog.id.in_(segment.ids)))
    if deleted.rowcount != len(segment.rows):
        raise DeleteMismatch(f"{segment.key}: deleted {deleted.rowcount} of {len(segment.rows)} rows")


async def archive_segment(
    store: AuditArchiveStore,
    segment: Segment,
    *,
    job_id: UUID,
    lease_token: UUID,
) -> None:
    """Upload, read back and verify a segment, then commit it in its own transaction."""
    await store.put(segment.key, segment.body)
    verify_segment(await store.get(segment.key), sha256=segment.sha256, ids=segment.ids)
    async with get_db_context() as db:
        await commit_segment(db, segment, job_id=job_id, lease_token=lease_token)


async def expire_segments(
    store: AuditArchiveStore,
    *,
    expiry: datetime,
    job_id: UUID,
    lease_token: UUID,
    limit: int = 100,
) -> tuple[int, int]:
    """Delete segments whose newest event is older than ``expiry``; returns (segments, rows).

    Each page deletes its objects inside the lease-holding transaction that
    removes their catalog rows, so a runner without the lease deletes nothing,
    and a failed object delete rolls the catalog back. ``limit`` stays small
    because the job row lock blocks this job's heartbeat until the page commits.
    """
    segments = rows = 0
    while True:
        async with get_db_context() as db:
            await _hold_lease(db, job_id, lease_token)
            page = (
                await db.execute(
                    select(AuditArchiveSegment.id)
                    .where(AuditArchiveSegment.last_created_at < expiry)
                    .order_by(AuditArchiveSegment.last_created_at, AuditArchiveSegment.id)
                    .limit(limit)
                )
            ).scalars().all()
            if not page:
                return segments, rows
            removed = (
                await db.execute(
                    delete(AuditArchiveSegment)
                    .where(AuditArchiveSegment.id.in_(page))
                    .returning(AuditArchiveSegment.object_key, AuditArchiveSegment.row_count)
                )
            ).all()
            for key, _ in removed:
                await store.delete(key)
        segments += len(removed)
        rows += sum(count for _, count in removed)


async def plan_expiry(db: AsyncSession, *, expiry: datetime | None) -> dict[str, Any]:
    """Events a run would delete at ``expiry``. JSON-ready.

    A run archives every event older than its cutoff before it expires, so
    events still in Postgres that are older than ``expiry`` are archived and
    deleted in the same run. They count alongside the cataloged segments, one
    segment per organization and UTC day.
    """
    if expiry is None:
        return {"expiring_segments": 0, "expiring_rows": 0, "expiring_from": None, "expiring_to": None}
    segments, archived, first_day, last_day = (
        await db.execute(
            select(
                func.count(AuditArchiveSegment.id),
                func.coalesce(func.sum(AuditArchiveSegment.row_count), 0),
                func.min(AuditArchiveSegment.day),
                func.max(AuditArchiveSegment.day),
            ).where(AuditArchiveSegment.last_created_at < expiry)
        )
    ).one()
    day = func.date_trunc("day", func.timezone("UTC", AuditLog.created_at))
    current, oldest, newest, current_segments = (
        await db.execute(
            select(
                func.count(),
                func.min(AuditLog.created_at),
                func.max(AuditLog.created_at),
                func.count(tuple_(AuditLog.organization_id, day).distinct()),
            ).where(AuditLog.created_at < expiry)
        )
    ).one()
    days = [d for d in (first_day, last_day) if d is not None] + [
        at.astimezone(timezone.utc).date() for at in (oldest, newest) if at is not None
    ]
    return {
        "expiring_segments": segments + current_segments,
        "expiring_rows": int(archived) + current,
        "expiring_from": min(days).isoformat() if days else None,
        "expiring_to": max(days).isoformat() if days else None,
    }


async def plan_archive(
    db: AsyncSession,
    *,
    cutoff: datetime,
    expiry: datetime | None,
) -> dict[str, Any]:
    """What a run would archive and expire, from SQL aggregates only. JSON-ready."""
    day = func.date_trunc("day", func.timezone("UTC", AuditLog.created_at)).label("day")
    eligible = (
        await db.execute(
            select(
                day,
                func.count().label("rows"),
                func.sum(func.pg_column_size(literal_column("audit_logs.*"))).label("bytes"),
            )
            .where(AuditLog.created_at < cutoff)
            .group_by(day)
            .order_by(day)
        )
    ).all()
    days = [{"day": d.date().isoformat(), "rows": n, "bytes": int(size)} for d, n, size in eligible]
    return {
        "cutoff": cutoff.isoformat(),
        "expiry": expiry.isoformat() if expiry else None,
        "eligible_rows": sum(d["rows"] for d in days),
        "estimated_bytes": sum(d["bytes"] for d in days),
        "days": days,
        **await plan_expiry(db, expiry=expiry),
    }


async def retention_info(db: AsyncSession, settings: AuditRetentionSettings) -> dict[str, Any]:
    """The configured windows plus what the database and the archive hold."""
    oldest = await db.scalar(select(func.min(AuditLog.created_at)))
    through, segments, rows = (
        await db.execute(
            select(
                func.max(AuditArchiveSegment.last_created_at),
                func.count(AuditArchiveSegment.id),
                func.coalesce(func.sum(AuditArchiveSegment.row_count), 0),
            )
        )
    ).one()
    return {
        "hot_days": settings.hot_days,
        "archive_days": settings.archive_days,
        "oldest_in_database": oldest,
        "archived_through": through,
        "archived_segments": segments,
        "archived_rows": int(rows),
    }


async def audit_retention_info(db: AsyncSession) -> AuditRetentionInfo:
    """The retention window as configured now, for callers that have no settings loaded."""
    settings = await AuditRetentionSettingsService(db).get_settings()
    return AuditRetentionInfo(**await retention_info(db, settings))


async def latest_audit_archive_job(db: AsyncSession) -> PlatformJob | None:
    """The newest ``audit.archive`` run, scheduled or manual, dry run or not."""
    return await db.scalar(
        select(PlatformJob)
        .where(PlatformJob.job_type == "audit.archive")
        .order_by(PlatformJob.created_at.desc())
        .limit(1)
    )
