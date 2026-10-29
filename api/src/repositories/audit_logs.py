"""
Audit log repository.

Read/write access to the audit_logs table. Writes come from shared/audit.py
via emit_audit(); reads are exposed via the /api/audit endpoint.
"""

import base64
import binascii
import builtins
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import ColumnElement, Select, String, Text, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.audit import AuditLog, named_workflow_id
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from shared.audit_aggregation import AggregateInterval, AggregateSelector

logger = logging.getLogger(__name__)


def _encode_cursor(created_at: datetime, log_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{log_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(token: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(token.encode()).decode()
    ts_str, id_str = raw.split("|", 1)
    return datetime.fromisoformat(ts_str), UUID(id_str)


GroupBy = Literal["workflow", "action", "resource_type", "outcome", "user", "organization"]
# An id to match, or "none" for entries without one.
IdOrNone = UUID | Literal["none"]
_MAX_GROUPS = 500
_AGGREGATE_INTERVAL_SQL = {
    "1h": text("INTERVAL '1 hour'"),
    "6h": text("INTERVAL '6 hours'"),
    "24h": text("INTERVAL '24 hours'"),
}
_AGGREGATE_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class AuditLogGroupRow:
    key: str | None
    count: int
    last_seen: datetime
    sample: AuditLog


@dataclass(frozen=True)
class AuditLogAggregateRow:
    selector: AggregateSelector
    count: int
    first_seen: datetime
    last_seen: datetime
    sample: AuditLog

    @property
    def window_start(self) -> datetime:
        return self.selector.window_start

    @property
    def window_end(self) -> datetime:
        return self.selector.window_end

    @property
    def resource_type(self) -> str | None:
        return self.selector.resource_type


def _group_key(group_by: GroupBy) -> ColumnElement[Any]:
    """The grouping key, as text (None when the entry has no value)."""
    if group_by == "workflow":
        return named_workflow_id()
    column = {
        "action": AuditLog.action,
        "resource_type": AuditLog.resource_type,
        "outcome": AuditLog.outcome,
        "user": AuditLog.user_id,
        "organization": AuditLog.organization_id,
    }[group_by]
    return cast(column, String)


def _aggregate_run_user_id() -> ColumnElement[str]:
    """The run user for access-check aggregates, even after its audit FK clears."""
    recorded = AuditLog.details["trace"]["steps"][0]["facts"]["user_id"].astext
    return func.coalesce(cast(AuditLog.user_id, String), recorded)


def _encode_aggregate_cursor(created_at: datetime, log_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"aggregate-v1|{created_at.isoformat()}|{log_id}".encode()).decode()


def _decode_aggregate_cursor(token: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.b64decode(token.encode(), altchars=b"-_", validate=True).decode()
        version, ts_str, id_str = raw.split("|", 2)
        if version != "aggregate-v1":
            raise ValueError("wrong aggregate cursor version")
        cursor_ts, cursor_id = datetime.fromisoformat(ts_str), UUID(id_str)
        if cursor_ts.tzinfo is None:
            raise ValueError("aggregate cursor needs a timezone")
        return cursor_ts, cursor_id
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise ValueError("invalid aggregate continuation token") from error


def _equal_or_null(column: ColumnElement[Any], value: Any) -> ColumnElement[bool]:
    return column.is_(None) if value is None else column == value


def _filtered(
    query: Select[Any],
    *,
    action_prefix: str | None,
    action_exact: str | None,
    resource_type: str | None,
    outcome: str | None,
    user_id: UUID | None,
    execution_id: UUID | None,
    workflow_id: IdOrNone | None,
    organization_id: IdOrNone | None,
    start_date: datetime | None,
    end_date: datetime | None,
    search: str | None,
    organizations: ColumnElement[bool] | None,
    aggregate_selector: AggregateSelector | None = None,
) -> Select[Any]:
    if action_exact:
        query = query.where(AuditLog.action == action_exact)
    elif action_prefix:
        query = query.where(AuditLog.action.startswith(action_prefix))
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if outcome:
        query = query.where(AuditLog.outcome == outcome)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if execution_id:
        query = query.where(AuditLog.execution_id == execution_id)
    if workflow_id == "none":
        query = query.where(named_workflow_id().is_(None))
    elif workflow_id:
        query = query.where(named_workflow_id() == str(workflow_id))
    if organization_id == "none":
        query = query.where(AuditLog.organization_id.is_(None))
    elif organization_id:
        query = query.where(AuditLog.organization_id == organization_id)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)
    if organizations is not None:
        query = query.where(organizations)
    if aggregate_selector is not None:
        query = query.where(
            AuditLog.created_at >= aggregate_selector.window_start,
            AuditLog.created_at < aggregate_selector.window_end,
            _equal_or_null(named_workflow_id(), aggregate_selector.workflow_id),
            _equal_or_null(AuditLog.resource_type, aggregate_selector.resource_type),
            _equal_or_null(AuditLog.organization_id, aggregate_selector.organization_id),
            _equal_or_null(AuditLog.operation_id, aggregate_selector.operation_id),
            _equal_or_null(
                _aggregate_run_user_id(),
                None if aggregate_selector.user_id is None else str(aggregate_selector.user_id),
            ),
            AuditLog.outcome == aggregate_selector.outcome,
        )
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
    return query


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
        execution_id: UUID | None,
        operation_id: str | None = None,
        surface: str | None = None,
        impersonated_by_user_id: UUID | None = None,
        impersonation_session_id: UUID | None = None,
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
            execution_id=execution_id,
            operation_id=operation_id,
            surface=surface,
            impersonated_by_user_id=impersonated_by_user_id,
            impersonation_session_id=impersonation_session_id,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def list(
        self,
        *,
        action_prefix: str | None = None,
        action_exact: str | None = None,
        resource_type: str | None = None,
        outcome: str | None = None,
        user_id: UUID | None = None,
        execution_id: UUID | None = None,
        workflow_id: IdOrNone | None = None,
        organization_id: IdOrNone | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        search: str | None = None,
        organizations: ColumnElement[bool] | None = None,
        aggregate_selector: AggregateSelector | None = None,
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

        query = _filtered(
            select(AuditLog),
            action_prefix=action_prefix,
            action_exact=action_exact,
            resource_type=resource_type,
            outcome=outcome,
            user_id=user_id,
            execution_id=execution_id,
            workflow_id=workflow_id,
            organization_id=organization_id,
            start_date=start_date,
            end_date=end_date,
            search=search,
            organizations=organizations,
            aggregate_selector=aggregate_selector,
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

    async def group(
        self,
        group_by: GroupBy,
        *,
        action_prefix: str | None = None,
        action_exact: str | None = None,
        resource_type: str | None = None,
        outcome: str | None = None,
        user_id: UUID | None = None,
        execution_id: UUID | None = None,
        workflow_id: IdOrNone | None = None,
        organization_id: IdOrNone | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        search: str | None = None,
        organizations: ColumnElement[bool] | None = None,
    ) -> builtins.list[AuditLogGroupRow]:
        """Matching entries grouped by ``group_by``: count, newest time and
        newest entry per group, largest groups first (at most 500)."""
        key = _group_key(group_by)
        filters = {
            "action_prefix": action_prefix,
            "action_exact": action_exact,
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
        counts = (
            await self.session.execute(
                _filtered(
                    select(key.label("key"), func.count().label("count"), func.max(AuditLog.created_at)),
                    **filters,
                )
                .group_by(key)
                .order_by(func.count().desc(), key)
                .limit(_MAX_GROUPS)
            )
        ).all()
        samples = (
            await self.session.execute(
                _filtered(select(key.label("key"), AuditLog), **filters)
                .distinct(key)
                .order_by(key, AuditLog.created_at.desc(), AuditLog.id.desc())
            )
        ).all()
        newest = {row.key: row.AuditLog for row in samples}
        return [
            AuditLogGroupRow(key=row.key, count=row.count, last_seen=row[2], sample=newest[row.key])
            for row in counts
        ]

    async def aggregate(
        self,
        interval: AggregateInterval,
        *,
        action_exact: str,
        resource_type: str | None = None,
        outcome: str | None = None,
        user_id: UUID | None = None,
        execution_id: UUID | None = None,
        workflow_id: IdOrNone | None = None,
        organization_id: IdOrNone | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        search: str | None = None,
        organizations: ColumnElement[bool] | None = None,
        limit: int = 50,
        continuation_token: str | None = None,
    ) -> tuple[builtins.list[AuditLogAggregateRow], str | None]:
        """Aggregate every matching access check into fixed UTC time windows."""
        bucket = func.date_bin(_AGGREGATE_INTERVAL_SQL[interval], AuditLog.created_at, _AGGREGATE_EPOCH).label(
            "window_start"
        )
        base = _filtered(
            select(
                AuditLog.id.label("id"),
                AuditLog.created_at.label("created_at"),
                bucket,
                named_workflow_id().label("workflow_id"),
                AuditLog.resource_type.label("resource_type"),
                AuditLog.organization_id.label("organization_id"),
                AuditLog.operation_id.label("operation_id"),
                _aggregate_run_user_id().label("run_user_id"),
                AuditLog.outcome.label("outcome"),
            ),
            action_prefix=None,
            action_exact=action_exact,
            resource_type=resource_type,
            outcome=outcome,
            user_id=user_id,
            execution_id=execution_id,
            workflow_id=workflow_id,
            organization_id=organization_id,
            start_date=start_date,
            end_date=end_date,
            search=search,
            organizations=organizations,
        ).subquery()
        dimensions = (
            base.c.window_start,
            base.c.workflow_id,
            base.c.resource_type,
            base.c.organization_id,
            base.c.operation_id,
            base.c.run_user_id,
            base.c.outcome,
        )
        ranked = select(
            base,
            func.count().over(partition_by=dimensions).label("count"),
            func.min(base.c.created_at).over(partition_by=dimensions).label("first_seen"),
            func.max(base.c.created_at).over(partition_by=dimensions).label("last_seen"),
            func.row_number().over(
                partition_by=dimensions, order_by=(base.c.created_at.desc(), base.c.id.desc())
            ).label("row_number"),
        ).subquery()
        query = select(ranked).where(ranked.c.row_number == 1)
        if continuation_token:
            cursor_ts, cursor_id = _decode_aggregate_cursor(continuation_token)
            query = query.where(
                or_(
                    ranked.c.last_seen < cursor_ts,
                    and_(ranked.c.last_seen == cursor_ts, ranked.c.id < cursor_id),
                )
            )
        limit = max(1, min(limit, 500))
        rows = (
            await self.session.execute(query.order_by(ranked.c.last_seen.desc(), ranked.c.id.desc()).limit(limit + 1))
        ).mappings().all()
        next_token = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_token = _encode_aggregate_cursor(last["last_seen"], last["id"])
            rows = rows[:limit]
        sample_rows = (
            await self.session.execute(select(AuditLog).where(AuditLog.id.in_([row["id"] for row in rows])))
        ).scalars().all()
        samples = {row.id: row for row in sample_rows}
        groups = [
            AuditLogAggregateRow(
                selector=AggregateSelector(
                    interval=interval,
                    window_start=row["window_start"],
                    workflow_id=row["workflow_id"],
                    resource_type=row["resource_type"],
                    organization_id=row["organization_id"],
                    operation_id=row["operation_id"],
                    user_id=None if row["run_user_id"] is None else UUID(row["run_user_id"]),
                    outcome=row["outcome"],
                ),
                count=row["count"],
                first_seen=row["first_seen"],
                last_seen=row["last_seen"],
                sample=samples[row["id"]],
            )
            for row in rows
        ]
        return groups, next_token
