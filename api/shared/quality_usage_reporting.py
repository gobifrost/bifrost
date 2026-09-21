"""Internal aggregation helpers for quality usage reporting.

This module returns plain dictionaries for later API DTO mapping. It does not
perform authorization, expose routes, enqueue jobs, or call providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, case, cast, func, literal, select, true, union_all
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.organizations import Organization

UsageScope = Literal["platform", "organization", "global_only"]
UsageSource = Literal["all", "executions", "chat", "agents"]

RUNTIME_EXECUTION = "runtime_execution"
RUNTIME_CHAT = "runtime_chat"
RUNTIME_AGENT = "runtime_agent"
AGENT_SUMMARY = "agent_summary"
TEST_DESIGNER = "test_designer"
SIMULATION = "simulation"
LEGACY_UNKNOWN = "legacy_unknown"

_ALLOWED_SOURCES = {"all", "executions", "chat", "agents"}
_VALID_UUID_RE = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ZERO = Decimal("0")
_TOTAL_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "call_count",
    "duration_ms",
    "duration_missing_count",
    "missing_cost_call_count",
    "legacy_call_count",
)


@dataclass(frozen=True)
class UsageReportPagination:
    limit: int = 50
    offset: int = 0

    def normalized(self) -> "UsageReportPagination":
        if self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.limit > 200:
            raise ValueError("limit must be at most 200")
        if self.offset < 0:
            raise ValueError("offset must be nonnegative")
        return self


def utc_inclusive_dates_to_half_open(
    start: date, end: date
) -> tuple[datetime, datetime]:
    """Convert inclusive UTC dates to an exclusive UTC timestamp window."""
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    try:
        exclusive_end = end + timedelta(days=1)
    except OverflowError as exc:
        raise ValueError("end_date is too large") from exc
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(exclusive_end, time.min, tzinfo=timezone.utc),
    )


@dataclass(frozen=True)
class QualityUsageReportFilters:
    scope: UsageScope
    organization_id: UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None  # Exclusive upper bound.
    source: UsageSource = "all"
    purpose: str | None = None
    provider: str | None = None
    model: str | None = None
    profile_id: UUID | None = None
    profile_fingerprint: str | None = None
    quality_operation_type: str | None = None
    quality_operation_id: UUID | None = None
    pagination: UsageReportPagination = UsageReportPagination()

    def validate(self) -> "QualityUsageReportFilters":
        if self.scope not in ("platform", "organization", "global_only"):
            raise ValueError("scope must be 'platform', 'organization', or 'global_only'")
        if self.scope == "organization" and self.organization_id is None:
            raise ValueError("organization scope requires organization_id")
        if self.scope in ("platform", "global_only") and self.organization_id is not None:
            raise ValueError(f"{self.scope} scope must not include organization_id")
        if self.source not in _ALLOWED_SOURCES:
            raise ValueError("source must be all, executions, chat, or agents")
        _validate_utc("start_at", self.start_at)
        _validate_utc("end_at", self.end_at)
        if self.start_at and self.end_at and self.start_at > self.end_at:
            raise ValueError("start_at must be before or equal to end_at")
        if (self.quality_operation_type is None) != (self.quality_operation_id is None):
            raise ValueError("quality operation filters require both type and id")
        self.pagination.normalized()
        return self


async def summarize_quality_usage(
    session: AsyncSession, filters: QualityUsageReportFilters
) -> dict:
    filters = filters.validate()
    overall = await _overall(session, filters)
    return {
        "overall": overall["totals"],
        "coverage": overall["coverage"],
        "by_purpose": await _dimension_page(session, filters, "purpose"),
        "by_provider_model": await _dimension_page(session, filters, "provider_model"),
        "by_profile": await _dimension_page(session, filters, "profile"),
        "by_organization": await _dimension_page(session, filters, "organization"),
        "by_operation": await _dimension_page(session, filters, "operation"),
    }


async def summarize_quality_operation_usage(
    session: AsyncSession,
    *,
    scope: UsageScope,
    operation_type: str,
    operation_id: UUID,
    organization_id: UUID | None = None,
    purpose: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    pagination: UsageReportPagination = UsageReportPagination(limit=100),
) -> dict:
    return await summarize_quality_usage(
        session,
        QualityUsageReportFilters(
            scope=scope,
            organization_id=organization_id,
            start_at=start_at,
            end_at=end_at,
            source="all",
            purpose=purpose,
            quality_operation_type=operation_type,
            quality_operation_id=operation_id,
            pagination=pagination,
        ),
    )


async def _overall(session: AsyncSession, filters: QualityUsageReportFilters) -> dict:
    usage = await _usage_total_row(session, filters)
    attempts = await _attempt_total_row(session, filters)
    totals = _totals_dict()
    coverage = _coverage_dict()
    if usage:
        _apply_usage_totals(totals, coverage, usage)
    if attempts:
        coverage["started_attempt_count"] += int(
            attempts.get("started_attempt_count") or 0
        )
        coverage["unobserved_attempt_count"] += int(
            attempts.get("unobserved_attempt_count") or 0
        )
    coverage["missing_cost_call_count"] = totals["missing_cost_call_count"]
    coverage["legacy_call_count"] = totals["legacy_call_count"]
    coverage["legacy_coverage_unknown"] = totals["legacy_call_count"] > 0
    return {"totals": totals, "coverage": coverage}


async def _usage_total_row(
    session: AsyncSession, filters: QualityUsageReportFilters
) -> dict | None:
    purpose = _usage_purpose_expr().label("purpose")
    operation_type = _usage_operation_type_expr(purpose).label("operation_type")
    operation_id = _usage_operation_id_expr(purpose).label("operation_id")
    profile_id = func.coalesce(AIUsage.profile_id, _runtime_profile_id_expr()).label(
        "profile_id"
    )
    conditions = _usage_conditions(
        filters, purpose, operation_type, operation_id, profile_id
    )
    query = (
        select(*_usage_aggregate_columns(purpose, operation_id))
        .select_from(AIUsage)
        .outerjoin(AgentRun, AIUsage.agent_run_id == AgentRun.id)
        .where(*conditions)
    )
    row = (await session.execute(query)).mappings().one_or_none()
    return dict(row) if row is not None else None


async def _attempt_total_row(
    session: AsyncSession, filters: QualityUsageReportFilters
) -> dict | None:
    if filters.source != "all":
        return None
    query = (
        select(
            func.count(AIUsageAttempt.id)
            .filter(AIUsageAttempt.state == "started")
            .label("started_attempt_count"),
            func.count(AIUsageAttempt.id)
            .filter(AIUsageAttempt.state == "unobserved")
            .label("unobserved_attempt_count"),
        )
        .select_from(AIUsageAttempt)
        .outerjoin(AIUsage, AIUsage.usage_attempt_id == AIUsageAttempt.id)
        .where(AIUsage.id.is_(None), *_attempt_conditions(filters))
    )
    row = (await session.execute(query)).mappings().one_or_none()
    return dict(row) if row is not None else None


async def _dimension_page(
    session: AsyncSession, filters: QualityUsageReportFilters, dimension: str
) -> dict:
    page = filters.pagination.normalized()
    projection = _dimension_union(filters, dimension).subquery()
    dims = _dimension_names(dimension)
    grouped = (
        select(
            *(getattr(projection.c, name).label(name) for name in dims),
            *(_sum_col(projection, key) for key in _TOTAL_KEYS),
            func.coalesce(
                func.sum(projection.c.observed_provider_cost), Decimal("0")
            ).label("observed_provider_cost"),
            func.coalesce(func.sum(projection.c.estimated_cost), Decimal("0")).label(
                "estimated_cost"
            ),
            func.coalesce(func.sum(projection.c.started_attempt_count), 0).label(
                "started_attempt_count"
            ),
            func.coalesce(func.sum(projection.c.unobserved_attempt_count), 0).label(
                "unobserved_attempt_count"
            ),
            func.coalesce(
                func.sum(projection.c.unassigned_operation_call_count), 0
            ).label("unassigned_operation_call_count"),
        )
        .select_from(projection)
        .group_by(*(getattr(projection.c, name) for name in dims))
    ).subquery()
    total_groups = (
        await session.execute(select(func.count()).select_from(grouped))
    ).scalar_one()
    known_cost = grouped.c.observed_provider_cost + grouped.c.estimated_cost
    gap_count = grouped.c.started_attempt_count + grouped.c.unobserved_attempt_count
    rows = (
        (
            await session.execute(
                select(grouped)
                .order_by(
                    known_cost.desc(),
                    gap_count.desc(),
                    *[getattr(grouped.c, name).asc().nulls_last() for name in dims],
                )
                .limit(page.limit)
                .offset(page.offset)
            )
        )
        .mappings()
        .all()
    )
    items = [_group_item(row, dims) for row in rows]
    return {
        "items": items,
        "total_groups": int(total_groups or 0),
        "limit": page.limit,
        "offset": page.offset,
        "omitted_group_count": max(
            int(total_groups or 0) - page.offset - len(items), 0
        ),
    }


def _dimension_union(filters: QualityUsageReportFilters, dimension: str):
    usage = _usage_dimension_select(filters, dimension)
    if filters.source != "all":
        return usage
    return union_all(usage, _attempt_dimension_select(filters, dimension))


def _usage_dimension_select(filters: QualityUsageReportFilters, dimension: str):
    purpose = _usage_purpose_expr().label("purpose")
    operation_type = _usage_operation_type_expr(purpose).label("operation_type")
    operation_id = _usage_operation_id_expr(purpose).label("operation_id")
    profile_id = func.coalesce(AIUsage.profile_id, _runtime_profile_id_expr()).label(
        "profile_id"
    )
    conditions = _usage_conditions(
        filters, purpose, operation_type, operation_id, profile_id
    )
    return (
        select(
            *_usage_dimension_columns(
                dimension, purpose, operation_type, operation_id, profile_id
            ),
            *_usage_union_value_columns(purpose, operation_id),
        )
        .select_from(AIUsage)
        .outerjoin(AgentRun, AIUsage.agent_run_id == AgentRun.id)
        .outerjoin(Organization, AIUsage.organization_id == Organization.id)
        .where(*conditions)
        .group_by(
            *_usage_group_columns(
                dimension, purpose, operation_type, operation_id, profile_id
            )
        )
    )


def _attempt_dimension_select(filters: QualityUsageReportFilters, dimension: str):
    conditions = _attempt_conditions(filters)
    return (
        select(
            *_attempt_dimension_columns(dimension),
            *_attempt_union_value_columns(),
        )
        .select_from(AIUsageAttempt)
        .outerjoin(AIUsage, AIUsage.usage_attempt_id == AIUsageAttempt.id)
        .outerjoin(Organization, AIUsageAttempt.organization_id == Organization.id)
        .where(AIUsage.id.is_(None), *conditions)
        .group_by(*_attempt_group_columns(dimension))
    )


def _usage_union_value_columns(purpose, operation_id):
    return [
        *_usage_aggregate_columns(purpose, operation_id),
        literal(0).label("started_attempt_count"),
        literal(0).label("unobserved_attempt_count"),
    ]


def _attempt_union_value_columns():
    # UNION aligns by position, not label. Keep the same order as
    # _usage_aggregate_columns, including the two monetary columns.
    return [
        *(
            _zero_col(key)
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "call_count",
                "duration_ms",
                "duration_missing_count",
            )
        ),
        literal(Decimal("0")).label("observed_provider_cost"),
        literal(Decimal("0")).label("estimated_cost"),
        literal(0).label("missing_cost_call_count"),
        literal(0).label("legacy_call_count"),
        literal(0).label("unassigned_operation_call_count"),
        func.count(AIUsageAttempt.id)
        .filter(AIUsageAttempt.state == "started")
        .label("started_attempt_count"),
        func.count(AIUsageAttempt.id)
        .filter(AIUsageAttempt.state == "unobserved")
        .label("unobserved_attempt_count"),
    ]


def _usage_purpose_expr() -> ColumnElement[str]:
    designer = AgentRun.correlation["evaluation_designer"].op("=")(
        func.to_jsonb(true())
    )
    return case(
        (AIUsage.usage_purpose.isnot(None), AIUsage.usage_purpose),
        (AIUsage.execution_id.isnot(None), literal(RUNTIME_EXECUTION)),
        (AIUsage.conversation_id.isnot(None), literal(RUNTIME_CHAT)),
        (
            and_(
                AIUsage.agent_run_id.isnot(None),
                AgentRun.trigger_type == "evaluation_synthetic",
                designer,
            ),
            literal(TEST_DESIGNER),
        ),
        (
            and_(
                AIUsage.agent_run_id.isnot(None),
                AgentRun.trigger_type == "evaluation_synthetic",
            ),
            literal(SIMULATION),
        ),
        (
            and_(AIUsage.agent_run_id.isnot(None), AIUsage.sequence == 0),
            literal(AGENT_SUMMARY),
        ),
        (AIUsage.agent_run_id.isnot(None), literal(RUNTIME_AGENT)),
        else_=literal(LEGACY_UNKNOWN),
    )


def _usage_operation_type_expr(
    purpose: ColumnElement[str],
) -> ColumnElement[str | None]:
    eval_id = AgentRun.correlation["evaluation_execution_id"].as_string()
    valid_eval = eval_id.op("~")(_VALID_UUID_RE)
    return case(
        (AIUsage.quality_operation_type.isnot(None), AIUsage.quality_operation_type),
        (purpose == TEST_DESIGNER, literal("quality_designer")),
        (and_(purpose == SIMULATION, valid_eval), literal("synthetic_evaluation")),
        else_=literal(None),
    )


def _usage_operation_id_expr(purpose: ColumnElement[str]) -> ColumnElement[UUID | None]:
    eval_id = AgentRun.correlation["evaluation_execution_id"].as_string()
    valid_eval = eval_id.op("~")(_VALID_UUID_RE)
    return case(
        (AIUsage.quality_operation_id.isnot(None), AIUsage.quality_operation_id),
        (purpose == TEST_DESIGNER, func.coalesce(AgentRun.root_run_id, AgentRun.id)),
        (and_(purpose == SIMULATION, valid_eval), cast(eval_id, PG_UUID(as_uuid=True))),
        else_=literal(None),
    )


def _runtime_profile_id_expr() -> ColumnElement[UUID | None]:
    raw = AgentRun.execution_snapshot["model"]["profile_id"].as_string()
    return case(
        (
            and_(AIUsage.sequence > 0, raw.op("~")(_VALID_UUID_RE)),
            cast(raw, PG_UUID(as_uuid=True)),
        ),
        else_=literal(None),
    )


def _usage_conditions(filters, purpose, operation_type, operation_id, profile_id):
    conditions = []
    if filters.scope == "organization":
        conditions.append(AIUsage.organization_id == filters.organization_id)
    elif filters.scope == "global_only":
        conditions.append(AIUsage.organization_id.is_(None))
    if filters.start_at is not None:
        conditions.append(AIUsage.timestamp >= filters.start_at)
    if filters.end_at is not None:
        conditions.append(AIUsage.timestamp < filters.end_at)
    if filters.source == "executions":
        conditions.append(AIUsage.execution_id.isnot(None))
    elif filters.source == "chat":
        conditions.append(AIUsage.conversation_id.isnot(None))
    elif filters.source == "agents":
        conditions.append(AIUsage.agent_run_id.isnot(None))
    if filters.purpose:
        conditions.append(purpose == filters.purpose)
    if filters.provider:
        conditions.append(AIUsage.provider == filters.provider)
    if filters.model:
        conditions.append(AIUsage.model == filters.model)
    if filters.profile_id:
        conditions.append(profile_id == filters.profile_id)
    if filters.profile_fingerprint:
        conditions.append(AIUsage.profile_fingerprint == filters.profile_fingerprint)
    if filters.quality_operation_type and filters.quality_operation_id:
        conditions.append(operation_type == filters.quality_operation_type)
        conditions.append(operation_id == filters.quality_operation_id)
    return conditions


def _attempt_conditions(filters):
    conditions = []
    if filters.scope == "organization":
        conditions.append(AIUsageAttempt.organization_id == filters.organization_id)
    elif filters.scope == "global_only":
        conditions.append(AIUsageAttempt.organization_id.is_(None))
    if filters.start_at is not None:
        conditions.append(AIUsageAttempt.started_at >= filters.start_at)
    if filters.end_at is not None:
        conditions.append(AIUsageAttempt.started_at < filters.end_at)
    if filters.purpose:
        conditions.append(AIUsageAttempt.usage_purpose == filters.purpose)
    if filters.provider:
        conditions.append(AIUsageAttempt.provider == filters.provider)
    if filters.model:
        conditions.append(AIUsageAttempt.model == filters.model)
    if filters.profile_id:
        conditions.append(AIUsageAttempt.profile_id == filters.profile_id)
    if filters.profile_fingerprint:
        conditions.append(
            AIUsageAttempt.profile_fingerprint == filters.profile_fingerprint
        )
    if filters.quality_operation_type and filters.quality_operation_id:
        conditions.append(
            AIUsageAttempt.quality_operation_type == filters.quality_operation_type
        )
        conditions.append(
            AIUsageAttempt.quality_operation_id == filters.quality_operation_id
        )
    return conditions


def _usage_aggregate_columns(purpose, operation_id):
    return [
        func.coalesce(func.sum(AIUsage.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(AIUsage.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(AIUsage.cache_read_tokens), 0).label(
            "cache_read_tokens"
        ),
        func.coalesce(func.sum(AIUsage.cache_write_tokens), 0).label(
            "cache_write_tokens"
        ),
        func.count(AIUsage.id).label("call_count"),
        func.coalesce(func.sum(AIUsage.duration_ms), 0).label("duration_ms"),
        func.count(AIUsage.id)
        .filter(AIUsage.duration_ms.is_(None))
        .label("duration_missing_count"),
        func.coalesce(
            func.sum(
                case(
                    (AIUsage.provider_cost.isnot(None), AIUsage.provider_cost),
                    else_=Decimal("0"),
                )
            ),
            Decimal("0"),
        ).label("observed_provider_cost"),
        func.coalesce(
            func.sum(
                case(
                    (
                        and_(AIUsage.provider_cost.is_(None), AIUsage.cost.isnot(None)),
                        AIUsage.cost,
                    ),
                    else_=Decimal("0"),
                )
            ),
            Decimal("0"),
        ).label("estimated_cost"),
        func.count(AIUsage.id)
        .filter(AIUsage.cost.is_(None))
        .label("missing_cost_call_count"),
        func.count(AIUsage.id)
        .filter(AIUsage.usage_attempt_id.is_(None))
        .label("legacy_call_count"),
        func.count(AIUsage.id)
        .filter(and_(purpose.in_([TEST_DESIGNER, SIMULATION]), operation_id.is_(None)))
        .label("unassigned_operation_call_count"),
    ]


def _dimension_names(dimension):
    return {
        "purpose": ["purpose"],
        "provider_model": ["purpose", "provider", "model"],
        "profile": [
            "purpose",
            "profile_id",
            "profile_name",
            "profile_fingerprint",
            "provider",
            "model",
        ],
        "organization": ["organization_id", "organization_name"],
        "operation": ["operation_type", "operation_id", "purpose"],
    }[dimension]


def _usage_dimension_columns(
    dimension, purpose, operation_type, operation_id, profile_id
):
    columns = {
        "purpose": [purpose],
        "provider_model": [
            purpose,
            AIUsage.provider.label("provider"),
            AIUsage.model.label("model"),
        ],
        "profile": [
            purpose,
            profile_id,
            AIUsage.profile_name.label("profile_name"),
            AIUsage.profile_fingerprint.label("profile_fingerprint"),
            AIUsage.provider.label("provider"),
            AIUsage.model.label("model"),
        ],
        "organization": [
            AIUsage.organization_id.label("organization_id"),
            Organization.name.label("organization_name"),
        ],
        "operation": [operation_type, operation_id, purpose],
    }[dimension]
    return columns


def _attempt_dimension_columns(dimension):
    columns = {
        "purpose": [AIUsageAttempt.usage_purpose.label("purpose")],
        "provider_model": [
            AIUsageAttempt.usage_purpose.label("purpose"),
            AIUsageAttempt.provider.label("provider"),
            AIUsageAttempt.model.label("model"),
        ],
        "profile": [
            AIUsageAttempt.usage_purpose.label("purpose"),
            AIUsageAttempt.profile_id.label("profile_id"),
            AIUsageAttempt.profile_name.label("profile_name"),
            AIUsageAttempt.profile_fingerprint.label("profile_fingerprint"),
            AIUsageAttempt.provider.label("provider"),
            AIUsageAttempt.model.label("model"),
        ],
        "organization": [
            AIUsageAttempt.organization_id.label("organization_id"),
            Organization.name.label("organization_name"),
        ],
        "operation": [
            AIUsageAttempt.quality_operation_type.label("operation_type"),
            AIUsageAttempt.quality_operation_id.label("operation_id"),
            AIUsageAttempt.usage_purpose.label("purpose"),
        ],
    }[dimension]
    return columns


def _usage_group_columns(dimension, purpose, operation_type, operation_id, profile_id):
    return _usage_dimension_columns(
        dimension, purpose, operation_type, operation_id, profile_id
    )


def _attempt_group_columns(dimension):
    return _attempt_dimension_columns(dimension)


def _sum_col(projection, key):
    return func.coalesce(func.sum(getattr(projection.c, key)), 0).label(key)


def _zero_col(key):
    if key in {"observed_provider_cost", "estimated_cost"}:
        return literal(Decimal("0")).label(key)
    return literal(0).label(key)


def _group_item(row, dims):
    item = {name: row[name] for name in dims}
    totals = _totals_dict()
    coverage = _coverage_dict(include_legacy=False)
    _apply_usage_totals(totals, coverage, row)
    coverage["missing_cost_call_count"] = totals["missing_cost_call_count"]
    coverage["started_attempt_count"] = int(row["started_attempt_count"] or 0)
    coverage["unobserved_attempt_count"] = int(row["unobserved_attempt_count"] or 0)
    item["totals"] = totals
    item["coverage"] = coverage
    return item


def _apply_usage_totals(totals, coverage, row) -> None:
    for key in _TOTAL_KEYS:
        totals[key] += int(row.get(key) or 0)
    totals["observed_provider_cost"] += _decimal(row.get("observed_provider_cost"))
    totals["estimated_cost"] += _decimal(row.get("estimated_cost"))
    totals["known_cost"] = totals["observed_provider_cost"] + totals["estimated_cost"]
    coverage["unassigned_operation_call_count"] += int(
        row.get("unassigned_operation_call_count") or 0
    )


def _totals_dict() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "call_count": 0,
        "duration_ms": 0,
        "duration_missing_count": 0,
        "observed_provider_cost": _ZERO,
        "estimated_cost": _ZERO,
        "known_cost": _ZERO,
        "missing_cost_call_count": 0,
        "legacy_call_count": 0,
    }


def _coverage_dict(*, include_legacy: bool = True) -> dict:
    result = {
        "started_attempt_count": 0,
        "unobserved_attempt_count": 0,
        "missing_cost_call_count": 0,
        "unassigned_operation_call_count": 0,
    }
    if include_legacy:
        result["legacy_coverage_unknown"] = False
        result["legacy_call_count"] = 0
    return result


def _decimal(value) -> Decimal:
    if value is None:
        return _ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _validate_utc(field: str, value: datetime | None) -> None:
    if value is None:
        return
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
