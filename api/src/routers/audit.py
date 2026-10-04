"""
Audit log router.

Read-only API over the audit_logs table. Platform Admins read everything.
Anyone else holding ``roleassignments.read`` (who may see who has what
access, e.g. Platform Operators) reads only report-only access checks
(``access.check*``) in the organizations that permission reaches (decision
R3b P2).
"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models import AuditLogActor, AuditLogEntry, AuditLogListResponse
from src.models import Organization as OrganizationORM
from src.models import User as UserORM
from src.models.contracts.audit import AuditLogGroup
from src.models.orm.audit import AuditLog
from src.repositories.audit_logs import AuditLogRepository, GroupBy
from src.services.audit_retention.archiver import audit_retention_info
from src.services.authorization.enforce import load_caller, operation_reach

ACCESS_CHECK_ACTIONS = "access.check"

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
    # None when the caller reaches everything (a Platform Admin).
    organizations = reach.where(AuditLog.organization_id)
    if organizations is not None and not (action or "").startswith(ACCESS_CHECK_ACTIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only access checks (action=access.check) are readable without Platform Admin",
        )
    repo = AuditLogRepository(db)
    filters = {
        "action_prefix": action,
        "resource_type": resource_type,
        "outcome": outcome,
        "user_id": user_id,
        "execution_id": execution_id,
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
