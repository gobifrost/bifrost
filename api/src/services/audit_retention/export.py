"""Export archived and current audit events as one gzip JSONL file.

An export runs as an ``audit.query`` platform job under the same resource lock
as ``audit.archive``, so no event is in both Postgres and an archived segment
while it reads. It streams: archived segments one at a time, then current rows
a page at a time, through one gzip stream into object storage.
"""

from __future__ import annotations

import zlib
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import or_, select, tuple_

from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobContext, PlatformJobFailure
from src.models.contracts.audit_retention import AuditExportRequest
from src.models.orm import AuditArchiveSegment, AuditLog, PlatformJob
from src.services.audit_retention.archiver import BATCH_ROWS, snapshot_query, snapshot_row
from src.services.audit_retention.format import (
    ArchiveRow,
    ArchiveVerifyError,
    decode_segment,
    encode_line,
    verify_checksum,
)
from src.services.audit_retention.store import AuditArchiveStore
from src.services.authorization.reach import OrgReach

EXPORT_TTL_DAYS = 7
EXPORT_PREFIX = "_audit_exports/"
EXPORT_SUFFIX = ".jsonl.gz"
# Callers who do not reach everything read only report-only access checks.
ACCESS_CHECK_ACTIONS = "access.check"


class ReachSnapshot(BaseModel):
    """An ``OrgReach`` as stored in a job payload."""

    everything: bool
    organization_ids: list[UUID]
    managed: bool
    include_global: bool

    @classmethod
    def of(cls, reach: OrgReach) -> ReachSnapshot:
        return cls(
            everything=reach.everything,
            organization_ids=sorted(reach.organization_ids),
            managed=reach.managed,
            include_global=reach.include_global,
        )

    def to_reach(self) -> OrgReach:
        return OrgReach(
            everything=self.everything,
            organization_ids=frozenset(self.organization_ids),
            managed=self.managed,
            include_global=self.include_global,
        )


class AuditQueryPayload(BaseModel):
    mode: Literal["export"] = "export"
    request: AuditExportRequest
    reach: ReachSnapshot


def export_key(job_id: UUID) -> str:
    return f"{EXPORT_PREFIX}{job_id}{EXPORT_SUFFIX}"


def line_matches(row: ArchiveRow, request: AuditExportRequest, reach: OrgReach) -> bool:
    """Whether ``row`` belongs in the export ``request`` made with ``reach``."""
    return (
        request.start_date <= row.created_at <= request.end_date
        and (request.action is None or row.action.startswith(request.action))
        and (request.organization_id is None or row.organization_id == request.organization_id)
        and reach.covers(row.organization_id)
        and (reach.everything or row.action.startswith(ACCESS_CHECK_ACTIONS))
    )


def _segment_rows(key: str, blob: bytes, sha256: str) -> list[ArchiveRow]:
    # The catalog keeps only the first and last ids, so the checksum is the check.
    try:
        verify_checksum(blob, sha256)
    except ArchiveVerifyError as exc:
        raise PlatformJobFailure("archive_corrupt", f"Archived segment {key} failed its checksum") from exc
    try:
        return decode_segment(blob)
    except ArchiveVerifyError as exc:
        raise PlatformJobFailure("archive_corrupt", f"Archived segment {key}: {exc}") from exc


async def build_export(
    context: PlatformJobContext, payload: AuditQueryPayload, store: AuditArchiveStore
) -> dict:
    request = payload.request
    reach = payload.reach.to_reach()
    key = export_key(context.job_id)

    segment_query = (
        select(AuditArchiveSegment.object_key, AuditArchiveSegment.sha256)
        .where(
            AuditArchiveSegment.first_created_at <= request.end_date,
            AuditArchiveSegment.last_created_at >= request.start_date,
        )
        .order_by(AuditArchiveSegment.day, AuditArchiveSegment.organization_id, AuditArchiveSegment.first_created_at)
    )
    row_query = snapshot_query().where(
        AuditLog.created_at >= request.start_date, AuditLog.created_at <= request.end_date
    )
    if request.action is not None:
        row_query = row_query.where(AuditLog.action.startswith(request.action))
    if request.organization_id is not None:
        segment_query = segment_query.where(AuditArchiveSegment.organization_id == request.organization_id)
        row_query = row_query.where(AuditLog.organization_id == request.organization_id)
    segment_reach = reach.where(AuditArchiveSegment.organization_id)
    if segment_reach is not None:
        segment_query = segment_query.where(segment_reach)
    row_reach = reach.where(AuditLog.organization_id)
    if row_reach is not None:
        row_query = row_query.where(row_reach)

    async with get_db_context() as db:
        segments = (await db.execute(segment_query)).all()

    rows = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal rows
        compressor = zlib.compressobj(wbits=31)

        def write(batch: list[ArchiveRow]) -> bytes:
            nonlocal rows
            matching = [row for row in batch if line_matches(row, request, reach)]
            rows += len(matching)
            return compressor.compress(b"".join(encode_line(row) for row in matching))

        for index, (object_key, sha256) in enumerate(segments, start=1):
            try:
                blob = await store.get(object_key)
            except FileNotFoundError as exc:
                raise PlatformJobFailure(
                    "archive_missing", f"Archived segment {object_key} is cataloged but not in storage"
                ) from exc
            chunk = write(_segment_rows(object_key, blob, sha256))
            if chunk:
                yield chunk
            await context.report(
                f"Exported {index} of {len(segments)} archived segments", current=index, total=len(segments)
            )

        await context.report("Exporting current events", current=len(segments), total=len(segments))
        after: tuple[datetime, UUID] | None = None
        while True:
            page_query = row_query
            if after is not None:
                page_query = page_query.where(tuple_(AuditLog.created_at, AuditLog.id) > tuple_(*after))
            async with get_db_context() as db:
                result = await db.execute(
                    page_query.order_by(AuditLog.created_at, AuditLog.id).limit(BATCH_ROWS)
                )
                page = [snapshot_row(*record) for record in result.all()]
            if not page:
                break
            chunk = write(page)
            if chunk:
                yield chunk
            after = (page[-1].created_at, page[-1].id)

        yield compressor.flush()

    _, size = await store.put_chunks(key, chunks())
    return {"rows": rows, "bytes": size, "segments": len(segments), "export_key": key}


def _job_id(key: str) -> UUID | None:
    try:
        return UUID(key.removeprefix(EXPORT_PREFIX).removesuffix(EXPORT_SUFFIX))
    except ValueError:
        return None


async def cleanup_expired_exports(store: AuditArchiveStore, *, older_than: datetime) -> int:
    """Delete exports whose job finished before ``older_than``, or that name no existing job."""
    keys = {key: _job_id(key) async for key in store.iter_keys(EXPORT_PREFIX)}
    job_ids = [job_id for job_id in keys.values() if job_id is not None]
    async with get_db_context() as db:
        kept = set(
            (
                await db.execute(
                    select(PlatformJob.id).where(
                        PlatformJob.id.in_(job_ids),
                        or_(PlatformJob.completed_at.is_(None), PlatformJob.completed_at >= older_than),
                    )
                )
            ).scalars()
        )
    removed = 0
    for key, job_id in keys.items():
        if job_id not in kept:
            await store.delete(key)
            removed += 1
    return removed
