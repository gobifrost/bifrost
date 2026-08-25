"""
Audit log repository.

Read/write access to the audit_logs table. Writes come from shared/audit.py
via emit_audit(); reads are exposed via the /api/audit endpoint.
"""

import base64
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Text, and_, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.audit import AuditLog
from src.models.orm.organizations import Organization
from src.models.orm.users import User

logger = logging.getLogger(__name__)


def _encode_cursor(created_at: datetime, log_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{log_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(token: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(token.encode()).decode()
    ts_str, id_str = raw.split("|", 1)
    return datetime.fromisoformat(ts_str), UUID(id_str)


class AuditLogRepository:
    """Repository for audit_logs table."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        action: str,
        user_id: UUID | None,
        organization_id: UUID | None,
        resource_type: str | None,
        resource_id: UUID | None,
        outcome: str,
        source: str,
        ip_address: str | None,
        user_agent: str | None,
        details: dict[str, Any] | None,
    ) -> AuditLog:
        """Insert a new audit log row."""
        log = AuditLog(
            action=action,
            user_id=user_id,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            source=source,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def list(
        self,
        *,
        action_prefix: str | None = None,
        resource_type: str | None = None,
        outcome: str | None = None,
        user_id: UUID | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        search: str | None = None,
        limit: int = 50,
        continuation_token: str | None = None,
    ) -> tuple[list[AuditLog], str | None]:
        """
        List audit log entries, newest first, with keyset pagination.

        action_prefix: matches `action` by prefix (e.g. "user." matches
        "user.create", "user.update", etc.)
        search: matches actor identity, organization, action, resource type,
        IP address, or serialized event details so Event Log context such as
        file paths and table names/IDs is searchable across every page.
        """
        limit = max(1, min(limit, 500))

        query = select(AuditLog)

        if action_prefix:
            query = query.where(AuditLog.action.startswith(action_prefix))
        if resource_type:
            query = query.where(AuditLog.resource_type == resource_type)
        if outcome:
            query = query.where(AuditLog.outcome == outcome)
        if user_id:
            query = query.where(AuditLog.user_id == user_id)
        if start_date:
            query = query.where(AuditLog.created_at >= start_date)
        if end_date:
            query = query.where(AuditLog.created_at <= end_date)
        if search:
            like = f"%{search}%"
            query = query.outerjoin(User, User.id == AuditLog.user_id).outerjoin(
                Organization,
                Organization.id == AuditLog.organization_id,
            )
            query = query.where(
                or_(
                    User.email.ilike(like),
                    User.name.ilike(like),
                    Organization.name.ilike(like),
                    AuditLog.action.ilike(like),
                    AuditLog.resource_type.ilike(like),
                    AuditLog.ip_address.ilike(like),
                    cast(AuditLog.details, Text).ilike(like),
                )
            )

        if continuation_token:
            try:
                cursor_ts, cursor_id = _decode_cursor(continuation_token)
                query = query.where(
                    or_(
                        AuditLog.created_at < cursor_ts,
                        and_(AuditLog.created_at == cursor_ts, AuditLog.id < cursor_id),
                    )
                )
            except (ValueError, IndexError) as e:
                logger.warning(f"Invalid audit log continuation token: {e}")

        # Fetch limit+1 to know if there's a next page
        query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)

        result = await self.session.execute(query)
        rows = list(result.scalars().all())

        next_token: str | None = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_token = _encode_cursor(last.created_at, last.id)
            rows = rows[:limit]

        return rows, next_token
