"""
Audit log router.

Read-only API over the audit_logs table, and exports of archived and current
events to a file. Platform Admins read everything. Anyone else holding
``roleassignments.read`` (who may see who has what access, e.g. Platform
Operators) reads only report-only access checks (``access.check*``) in the
organizations that permission reaches (decision R3b P2).
"""

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.jobs.platform.audit_query import AUDIT_QUERY_DEFINITION, enqueue_audit_export
from src.models import AuditLogActor, AuditLogEntry, AuditLogListResponse
from src.models import Organization as OrganizationORM
from src.models import User as UserORM
from src.models.contracts.access_checks import AccessExplanation, AccessTrace
from src.models.contracts.audit import AuditLogGroup
from src.models.contracts.audit_retention import AuditExportRequest
from src.models.contracts.platform_jobs import PlatformJobAccepted
from src.models.orm.audit import AuditLog
from src.models.orm.platform_jobs import PlatformJob
from src.repositories.audit_logs import AuditLogRepository, GroupBy, IdOrNone
from src.services.access_explain import rerun, stored_trace
from src.services.audit_retention.archiver import audit_retention_info
from src.services.audit_retention.export import (
    ACCESS_CHECK_ACTIONS,
    EXPORT_TTL_DAYS,
    AuditQueryPayload,
    ReachSnapshot,
)
from src.services.audit_retention.settings import AuditRetentionSettingsService
from src.services.audit_retention.store import AuditArchiveStore
from src.services.authorization.enforce import load_caller, operation_reach
from src.services.authorization.reach import OrgReach

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["Audit"])


@router.get(
    "",
    response_model=AuditLogListResponse,
    summary="List audit log entries",
    description=(
        "List audit log entries with filters, or group them with group_by. Platform Admins read "
        "everything; Platform Operators read access checks in the organizations they reach."
    ),
)
async def list_audit_logs(
    user: CurrentActiveUser,
    db: DbSession,
    action: str | None = Query(None, description="Action prefix filter, e.g. 'user.' or 'auth.login'"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    outcome: str | None = Query(None, description="Filter by outcome: 'success' or 'failure'"),
    user_id: UUID | None = Query(None, description="Filter by acting user ID"),
    execution_id: UUID | None = Query(
        None, description="Filter by workflow execution ID"
    ),
    workflow_id: IdOrNone | None = Query(
        None, description="Filter by the workflow an entry names, or 'none' for entries naming no workflow"
    ),
    organization_id: IdOrNone | None = Query(
        None, description="Filter by organization ID, or 'none' for Global entries"
    ),
    start_date: datetime | None = Query(None, description="Start of time range (inclusive)"),
    end_date: datetime | None = Query(None, description="End of time range (inclusive)"),
    search: str | None = Query(
        None,
        description="Free-text search on actor, organization, action, resource type, IP address, and event details",
    ),
    group_by: GroupBy | None = Query(
        None, description="Group matching entries by this field (counts and newest entry per group)"
    ),
    limit: int = Query(50, ge=1, le=500),
    continuation_token: str | None = Query(None, description="Pagination cursor"),
) -> AuditLogListResponse:
    """List audit log entries, newest first, with keyset pagination."""
    reach = operation_reach(await load_caller(db, user), "GET /api/audit")
    _require_readable(reach, action)
    # None when the caller reaches everything (a Platform Admin).
    organizations = reach.where(AuditLog.organization_id)
    repo = AuditLogRepository(db)
    filters = {
        "action_prefix": action,
        "resource_type": resource_type,
        "outcome": outcome,
        "user_id": user_id,
        "execution_id": execution_id,
        "workflow_id": workflow_id,
        "organization_id": organization_id,
        "start_date": start_date,
        "end_date": end_date,
        "search": search,
        "organizations": organizations,
    }

    # The retention window is platform-wide, so only a caller who reaches everything sees it.
    retention = await audit_retention_info(db) if organizations is None else None

    if group_by is not None:
        grouped = await repo.group(group_by, **filters)
        samples = await _entries(db, [group.sample for group in grouped])
        return AuditLogListResponse(
            entries=[],
            groups=[
                AuditLogGroup(key=group.key, count=group.count, last_seen=group.last_seen, sample=sample)
                for group, sample in zip(grouped, samples, strict=True)
            ],
            retention=retention,
        )

    rows, next_token = await repo.list(**filters, limit=limit, continuation_token=continuation_token)
    return AuditLogListResponse(
        entries=await _entries(db, rows), continuation_token=next_token, retention=retention
    )


@router.post(
    "/exports",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Export audit events, archived and current, to a file",
    description=(
        "Queue an export of the audit events in a date range (at most 366 days) to one gzip JSONL "
        "file, under the same rules as listing them."
    ),
)
async def start_audit_export(
    body: AuditExportRequest,
    response: Response,
    user: CurrentActiveUser,
    db: DbSession,
) -> PlatformJobAccepted:
    reach = operation_reach(await load_caller(db, user), "POST /api/audit/exports")
    _require_readable(reach, body.action)
    if body.organization_id is not None and not reach.covers(body.organization_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot read audit events in that organization",
        )
    job = await enqueue_audit_export(db, user, body, reach)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(job_id=job.id, notification_id=job.notification_id, status=job.status)


@router.get(
    "/exports/{job_id}/download",
    response_class=StreamingResponse,
    summary="Download a finished audit export",
    responses={200: {"content": {"application/gzip": {}}}},
)
async def download_audit_export(job_id: UUID, user: CurrentActiveUser, db: DbSession) -> StreamingResponse:
    current = operation_reach(await load_caller(db, user), "GET /api/audit/exports/{job_id}/download")
    job = await db.get(PlatformJob, job_id)
    if (
        job is None
        or job.job_type != AUDIT_QUERY_DEFINITION.job_type
        or job.status != "succeeded"
        or job.requested_by_user_id != str(user.user_id)
        or job.completed_at is None
        or job.result is None
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export not found")
    if job.completed_at < datetime.now(timezone.utc) - timedelta(days=EXPORT_TTL_DAYS):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Export expired; run it again.")
    payload = AuditQueryPayload.model_validate(job.payload)
    if ReachSnapshot.of(current) != payload.reach and not current.everything:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your access changed since this export was made; run it again.",
        )
    start = payload.request.start_date.date().isoformat()
    end = payload.request.end_date.date().isoformat()
    # Read the first chunk before answering, so a missing file is a 404, not a truncated 200.
    chunks = AuditArchiveStore(get_settings()).iter_chunks(job.result["export_key"])
    try:
        first = await anext(chunks, b"")
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Export file not found; run it again."
        )

    async def body() -> AsyncIterator[bytes]:
        yield first
        async for chunk in chunks:
            yield chunk

    return StreamingResponse(
        body(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="audit-export-{start}-{end}.jsonl.gz"'},
    )


@router.get(
    "/{event_id}/explain",
    response_model=AccessExplanation,
    summary="Explain an access check",
    description=(
        "A stored access check as decided then, and judged again now against the run user's current "
        "roles and the workflow's current powers. Platform Operators explain access checks in the "
        "organizations they reach."
    ),
)
async def explain_access_check(event_id: UUID, user: CurrentActiveUser, db: DbSession) -> AccessExplanation:
    reach = operation_reach(await load_caller(db, user), "GET /api/audit/{event_id}/explain")
    row = await db.get(AuditLog, event_id)
    if row is None:
        hot_days = (await AuditRetentionSettingsService(db).get_settings()).hot_days
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Audit event not found. Events older than {hot_days} days are archived; "
                "export that day from the audit log to see them."
            ),
        )
    if not reach.everything and (row.action != "access.check" or not reach.covers(row.organization_id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot read this audit event")
    if row.action != "access.check":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only access checks can be explained"
        )
    then = stored_trace(row)
    now, now_unavailable = await rerun(db, row)
    return AccessExplanation(
        event=(await _entries(db, [row]))[0],
        then=then,
        now=None if now is None else AccessTrace.model_validate(now.as_dict()),
        now_unavailable=now_unavailable,
        changed=None if now is None or then is None else now.outcome != then.outcome,
    )


def _require_readable(reach: OrgReach, action: str | None) -> None:
    """Callers who do not reach everything read only access checks."""
    if not reach.everything and not (action or "").startswith(ACCESS_CHECK_ACTIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only access checks (action=access.check) are readable without Platform Admin",
        )


async def _entries(db: AsyncSession, rows: list[AuditLog]) -> list[AuditLogEntry]:
    """Entries for ``rows``, with actor user and organization names."""
    # Look up actor user + org names in batch for display.
    user_ids = {r.user_id for r in rows if r.user_id}
    org_ids = {r.organization_id for r in rows if r.organization_id}

    users_by_id: dict[UUID, UserORM] = {}
    if user_ids:
        result = await db.execute(select(UserORM).where(UserORM.id.in_(user_ids)))
        users_by_id = {u.id: u for u in result.scalars().all()}

    orgs_by_id: dict[UUID, OrganizationORM] = {}
    if org_ids:
        result = await db.execute(select(OrganizationORM).where(OrganizationORM.id.in_(org_ids)))
        orgs_by_id = {o.id: o for o in result.scalars().all()}

    entries: list[AuditLogEntry] = []
    for row in rows:
        actor_user = users_by_id.get(row.user_id) if row.user_id else None
        actor_org = orgs_by_id.get(row.organization_id) if row.organization_id else None
        entries.append(
            AuditLogEntry(
                id=row.id,
                timestamp=row.created_at,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                outcome=row.outcome,
                source=row.source,
                operation_id=row.operation_id,
                surface=row.surface,
                execution_id=row.execution_id,
                actor=AuditLogActor(
                    user_id=row.user_id,
                    user_email=actor_user.email if actor_user else None,
                    user_name=actor_user.name if actor_user else None,
                    organization_id=row.organization_id,
                    organization_name=actor_org.name if actor_org else None,
                ),
                ip_address=row.ip_address,
                user_agent=row.user_agent,
                details=row.details,
            )
        )
    return entries
