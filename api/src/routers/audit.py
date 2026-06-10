"""
Audit log router.

Read-only API over the audit_logs table. Platform admin only.
"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.models import AuditLogActor, AuditLogEntry, AuditLogListResponse
from src.models import Organization as OrganizationORM
from src.models import User as UserORM
from src.repositories.audit_logs import AuditLogRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["Audit"])


@router.get(
    "",
    response_model=AuditLogListResponse,
    summary="List audit log entries",
    description="List audit log entries with filters (Platform admin only)",
)
async def list_audit_logs(
    user: CurrentSuperuser,
    db: DbSession,
    action: str | None = Query(None, description="Action prefix filter, e.g. 'user.' or 'auth.login'"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    outcome: str | None = Query(None, description="Filter by outcome: 'success' or 'failure'"),
    user_id: UUID | None = Query(None, description="Filter by acting user ID"),
    start_date: datetime | None = Query(None, description="Start of time range (inclusive)"),
    end_date: datetime | None = Query(None, description="End of time range (inclusive)"),
    search: str | None = Query(None, description="Free-text search on action/resource_type"),
    limit: int = Query(50, ge=1, le=500),
    continuation_token: str | None = Query(None, description="Pagination cursor"),
) -> AuditLogListResponse:
    """List audit log entries, newest first, with keyset pagination."""
    del user  # auth gate only
    repo = AuditLogRepository(db)

    rows, next_token = await repo.list(
        action_prefix=action,
        resource_type=resource_type,
        outcome=outcome,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        search=search,
        limit=limit,
        continuation_token=continuation_token,
    )

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

    return AuditLogListResponse(entries=entries, continuation_token=next_token)
