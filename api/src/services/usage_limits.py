"""Provider-neutral usage-limit evaluation primitives.

This module intentionally contains no persistence or pricing logic.  It codifies
the shared limit semantics for Chat, Builder, autonomous runs, and future
sandbox providers in dimensions Bifrost can measure consistently across model
and runtime vendors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.ai_usage import (
    UsageLimitAggregateStatus,
    UsageLimitCeilings as UsageLimitCeilingsDTO,
    UsageLimitDimensionStatus,
    UsageLimitEffectiveResponse,
    UsageLimitPolicyPublic,
)


class UsageLimitScope(StrEnum):
    """Supported hierarchy for usage-limit policies."""

    PLATFORM = "platform"
    ORGANIZATION = "organization"
    USER = "user"
    SOLUTION = "solution"


class UsageLimitPeriod(StrEnum):
    """Supported aggregate usage windows."""

    DAILY = "daily"
    MONTHLY = "monthly"


SCOPE_PRECEDENCE: tuple[UsageLimitScope, ...] = (
    UsageLimitScope.SOLUTION,
    UsageLimitScope.USER,
    UsageLimitScope.ORGANIZATION,
    UsageLimitScope.PLATFORM,
)


@dataclass(frozen=True)
class PortableUsage:
    """Usage dimensions that do not depend on a provider's dollar formula."""

    model_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    runner_duration_ms: int = 0
    sandbox_compute_ms: int = 0

    @property
    def total_tokens(self) -> int:
        """Canonical total tokens.

        Provider/Pydantic usage reports cache tokens as input-token breakdowns,
        not additional usage on top of input. Cache dimensions remain separately
        enforceable, but total-token ceilings must not double-count them.
        """

        return self.input_tokens + self.output_tokens

    def __add__(self, other: Self) -> Self:
        return type(self)(
            model_requests=self.model_requests + other.model_requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            runner_duration_ms=self.runner_duration_ms + other.runner_duration_ms,
            sandbox_compute_ms=self.sandbox_compute_ms + other.sandbox_compute_ms,
        )

    def value_for(self, dimension: str) -> int:
        if dimension == "total_tokens":
            return self.total_tokens
        value = getattr(self, dimension)
        if not isinstance(value, int):
            raise AttributeError(dimension)
        return value


USAGE_DIMENSIONS: frozenset[str] = frozenset(
    {
        "model_requests",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        "runner_duration_ms",
        "sandbox_compute_ms",
    }
)


@dataclass(frozen=True)
class UsageCeilings:
    """Optional ceilings for portable usage dimensions."""

    model_requests: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    runner_duration_ms: int | None = None
    sandbox_compute_ms: int | None = None

    def configured(self) -> dict[str, int]:
        values: dict[str, int] = {}
        for dimension in USAGE_DIMENSIONS:
            value = getattr(self, dimension)
            if value is not None:
                values[dimension] = value
        return values

    def has_any(self) -> bool:
        return bool(self.configured())


@dataclass(frozen=True)
class UsageLimitPolicy:
    """One policy row projected from whatever storage layer owns limits."""

    scope: UsageLimitScope
    per_run: UsageCeilings = UsageCeilings()
    aggregate: UsageCeilings = UsageCeilings()
    aggregate_period: UsageLimitPeriod = UsageLimitPeriod.MONTHLY


@dataclass(frozen=True)
class UsageLimitSubject:
    """The concrete hierarchy to evaluate for one user/run."""

    organization_id: UUID | None = None
    user_id: UUID | None = None
    solution_id: UUID | None = None


@dataclass(frozen=True)
class UsageLimitViolation:
    """A single exceeded limit with enough provenance for UI/API messages."""

    scope: UsageLimitScope
    dimension: str
    limit: int
    projected: int
    current: int
    requested: int
    kind: str


@dataclass(frozen=True)
class UsageLimitDecision:
    """Result of evaluating one proposed usage increment."""

    allowed: bool
    per_run_scope: UsageLimitScope | None
    effective_per_run: UsageCeilings
    violations: tuple[UsageLimitViolation, ...] = ()


def resolve_effective_per_run_limit(
    policies: list[UsageLimitPolicy],
) -> tuple[UsageLimitScope | None, UsageCeilings]:
    """Return the most-specific configured per-run policy.

    The winner is the first scope in Solution > User > Organization > Platform
    order that has at least one configured per-run ceiling.
    """

    by_scope = {policy.scope: policy for policy in policies}
    for scope in SCOPE_PRECEDENCE:
        policy = by_scope.get(scope)
        if policy is not None and policy.per_run.has_any():
            return scope, policy.per_run
    return None, UsageCeilings()


def evaluate_usage_limits(
    *,
    policies: list[UsageLimitPolicy],
    current_per_run: PortableUsage,
    requested: PortableUsage,
    aggregate_usage_by_scope_period: dict[
        tuple[UsageLimitScope, UsageLimitPeriod],
        PortableUsage,
    ],
) -> UsageLimitDecision:
    """Evaluate a proposed usage increment against hierarchical limits.

    Per-run limits are most-specific-wins. Aggregate limits are cumulative:
    every configured parent or leaf aggregate ceiling must admit the projected
    total for its scope.
    """

    per_run_scope, per_run_limit = resolve_effective_per_run_limit(policies)
    violations: list[UsageLimitViolation] = []
    projected_per_run = current_per_run + requested
    if per_run_scope is not None:
        for dimension, limit in per_run_limit.configured().items():
            projected = projected_per_run.value_for(dimension)
            if projected > limit:
                violations.append(
                    UsageLimitViolation(
                        scope=per_run_scope,
                        dimension=dimension,
                        limit=limit,
                        projected=projected,
                        current=current_per_run.value_for(dimension),
                        requested=requested.value_for(dimension),
                        kind="per_run",
                    )
                )

    for policy in policies:
        current_scope_usage = aggregate_usage_by_scope_period.get(
            (policy.scope, policy.aggregate_period),
            PortableUsage(),
        )
        projected_scope_usage = current_scope_usage + requested
        for dimension, limit in policy.aggregate.configured().items():
            projected = projected_scope_usage.value_for(dimension)
            if projected > limit:
                violations.append(
                    UsageLimitViolation(
                        scope=policy.scope,
                        dimension=dimension,
                        limit=limit,
                        projected=projected,
                        current=current_scope_usage.value_for(dimension),
                        requested=requested.value_for(dimension),
                        kind="aggregate",
                    )
                )

    return UsageLimitDecision(
        allowed=not violations,
        per_run_scope=per_run_scope,
        effective_per_run=per_run_limit,
        violations=tuple(violations),
    )


def usage_scope_key(
    scope: UsageLimitScope,
    *,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    solution_id: UUID | None = None,
) -> str:
    """Return the durable ledger/policy key for a concrete scope."""

    match scope:
        case UsageLimitScope.PLATFORM:
            return "platform"
        case UsageLimitScope.ORGANIZATION:
            if organization_id is None:
                raise ValueError("organization_id is required for organization scope")
            return str(organization_id)
        case UsageLimitScope.USER:
            if user_id is None:
                raise ValueError("user_id is required for user scope")
            return str(user_id)
        case UsageLimitScope.SOLUTION:
            if solution_id is None:
                raise ValueError("solution_id is required for solution scope")
            return str(solution_id)


def usage_day(at: datetime | None = None) -> date:
    """Return the UTC daily usage bucket for a timestamp."""

    timestamp = at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).date()


def usage_period_start(period: UsageLimitPeriod, at: datetime | None = None) -> date:
    """Return the UTC start date for an aggregate usage period."""

    day = usage_day(at)
    if period == UsageLimitPeriod.DAILY:
        return day
    if period == UsageLimitPeriod.MONTHLY:
        return day.replace(day=1)
    raise ValueError(period)


def usage_subject_for_scope(
    scope: UsageLimitScope,
    *,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    solution_id: UUID | None = None,
) -> UsageLimitSubject:
    """Build the exact subject hierarchy for one managed policy target."""

    match scope:
        case UsageLimitScope.PLATFORM:
            return UsageLimitSubject()
        case UsageLimitScope.ORGANIZATION:
            if organization_id is None:
                raise ValueError("organization_id is required for organization scope")
            return UsageLimitSubject(organization_id=organization_id)
        case UsageLimitScope.USER:
            if user_id is None:
                raise ValueError("user_id is required for user scope")
            return UsageLimitSubject(
                organization_id=organization_id,
                user_id=user_id,
            )
        case UsageLimitScope.SOLUTION:
            if solution_id is None:
                raise ValueError("solution_id is required for solution scope")
            return UsageLimitSubject(
                organization_id=organization_id,
                solution_id=solution_id,
            )


def _ceilings_from_mapping(values: dict | None) -> UsageCeilings:
    values = values or {}
    unknown = set(values) - USAGE_DIMENSIONS
    if unknown:
        raise ValueError(f"Unknown usage dimensions: {', '.join(sorted(unknown))}")
    return UsageCeilings(
        **{dimension: int(value) for dimension, value in values.items()}
    )


def _ceilings_to_dto(ceilings: UsageCeilings) -> UsageLimitCeilingsDTO:
    return UsageLimitCeilingsDTO(**ceilings.configured())


def _usage_to_dto(usage: PortableUsage) -> UsageLimitCeilingsDTO:
    return UsageLimitCeilingsDTO(
        model_requests=usage.model_requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        total_tokens=usage.total_tokens,
        runner_duration_ms=usage.runner_duration_ms,
        sandbox_compute_ms=usage.sandbox_compute_ms,
    )


def _policy_to_public(row: object) -> UsageLimitPolicyPublic:
    return UsageLimitPolicyPublic(
        id=getattr(row, "id"),
        scope=getattr(row, "scope"),
        scope_key=getattr(row, "scope_key"),
        organization_id=getattr(row, "organization_id"),
        user_id=getattr(row, "user_id"),
        solution_id=getattr(row, "solution_id"),
        per_run=_ceilings_to_dto(_ceilings_from_row(row, "per_run_ceilings")),
        aggregate=_ceilings_to_dto(_ceilings_from_row(row, "aggregate_ceilings")),
        aggregate_period=getattr(row, "aggregate_period"),
        created_at=getattr(row, "created_at"),
        updated_at=getattr(row, "updated_at"),
    )


def _ceilings_from_row(row: object, attribute: str) -> UsageCeilings:
    return _ceilings_from_mapping(getattr(row, attribute))


def _usage_from_row(row: object) -> PortableUsage:
    return PortableUsage(
        model_requests=getattr(row, "model_requests"),
        input_tokens=getattr(row, "input_tokens"),
        output_tokens=getattr(row, "output_tokens"),
        cache_read_tokens=getattr(row, "cache_read_tokens"),
        cache_write_tokens=getattr(row, "cache_write_tokens"),
        runner_duration_ms=getattr(row, "runner_duration_ms"),
        sandbox_compute_ms=getattr(row, "sandbox_compute_ms"),
    )


def _scopes_for_subject(subject: UsageLimitSubject) -> tuple[UsageLimitScope, ...]:
    scopes: list[UsageLimitScope] = [UsageLimitScope.PLATFORM]
    if subject.organization_id is not None:
        scopes.append(UsageLimitScope.ORGANIZATION)
    if subject.user_id is not None:
        scopes.append(UsageLimitScope.USER)
    if subject.solution_id is not None:
        scopes.append(UsageLimitScope.SOLUTION)
    return tuple(scopes)


def _scope_ids(
    scope: UsageLimitScope,
    subject: UsageLimitSubject,
) -> tuple[UUID | None, UUID | None, UUID | None]:
    match scope:
        case UsageLimitScope.PLATFORM:
            return None, None, None
        case UsageLimitScope.ORGANIZATION:
            return subject.organization_id, None, None
        case UsageLimitScope.USER:
            return subject.organization_id, subject.user_id, None
        case UsageLimitScope.SOLUTION:
            return subject.organization_id, subject.user_id, subject.solution_id


def _build_ledger_upsert_statement(
    ledger_model: type,
    *,
    period: UsageLimitPeriod,
    period_start: date,
    scope: UsageLimitScope,
    scope_key: str,
    organization_id: UUID | None,
    user_id: UUID | None,
    solution_id: UUID | None,
    usage: PortableUsage,
    updated_at: datetime,
):
    statement = insert(ledger_model).values(
        period=period.value,
        period_start=period_start,
        scope=scope.value,
        scope_key=scope_key,
        organization_id=organization_id,
        user_id=user_id,
        solution_id=solution_id,
        model_requests=usage.model_requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        runner_duration_ms=usage.runner_duration_ms,
        sandbox_compute_ms=usage.sandbox_compute_ms,
        updated_at=updated_at,
    )
    excluded = statement.excluded
    return statement.on_conflict_do_update(
        index_elements=["period", "period_start", "scope", "scope_key"],
        set_={
            "model_requests": ledger_model.model_requests + excluded.model_requests,
            "input_tokens": ledger_model.input_tokens + excluded.input_tokens,
            "output_tokens": ledger_model.output_tokens + excluded.output_tokens,
            "cache_read_tokens": ledger_model.cache_read_tokens
            + excluded.cache_read_tokens,
            "cache_write_tokens": ledger_model.cache_write_tokens
            + excluded.cache_write_tokens,
            "runner_duration_ms": ledger_model.runner_duration_ms
            + excluded.runner_duration_ms,
            "sandbox_compute_ms": ledger_model.sandbox_compute_ms
            + excluded.sandbox_compute_ms,
            "updated_at": updated_at,
        },
    )


async def load_usage_limit_policies(
    session: AsyncSession,
    subject: UsageLimitSubject,
) -> list[UsageLimitPolicy]:
    """Load configured policies relevant to a subject hierarchy."""

    from src.models.orm.ai_usage import UsageLimitPolicyORM

    filters: list[tuple[str, str]] = []
    for scope in _scopes_for_subject(subject):
        filters.append(
            (
                scope.value,
                usage_scope_key(
                    scope,
                    organization_id=subject.organization_id,
                    user_id=subject.user_id,
                    solution_id=subject.solution_id,
                ),
            )
        )

    if not filters:
        return []

    conditions = [
        (UsageLimitPolicyORM.scope == scope)
        & (UsageLimitPolicyORM.scope_key == scope_key)
        for scope, scope_key in filters
    ]
    result = await session.execute(select(UsageLimitPolicyORM).where(or_(*conditions)))

    rows = result.scalars().all()
    return [
        UsageLimitPolicy(
            scope=UsageLimitScope(row.scope),
            per_run=_ceilings_from_row(row, "per_run_ceilings"),
            aggregate=_ceilings_from_row(row, "aggregate_ceilings"),
            aggregate_period=UsageLimitPeriod(row.aggregate_period),
        )
        for row in rows
    ]


async def list_usage_limit_policy_rows(
    session: AsyncSession,
    subject: UsageLimitSubject,
) -> list[UsageLimitPolicyPublic]:
    """List configured policy rows for a concrete selected boundary/subject."""

    from src.models.orm.ai_usage import UsageLimitPolicyORM

    conditions = [
        (UsageLimitPolicyORM.scope == scope.value)
        & (
            UsageLimitPolicyORM.scope_key
            == usage_scope_key(
                scope,
                organization_id=subject.organization_id,
                user_id=subject.user_id,
                solution_id=subject.solution_id,
            )
        )
        for scope in _scopes_for_subject(subject)
    ]
    result = await session.execute(
        select(UsageLimitPolicyORM)
        .where(or_(*conditions))
        .order_by(UsageLimitPolicyORM.scope, UsageLimitPolicyORM.scope_key)
    )
    return [_policy_to_public(row) for row in result.scalars().all()]


async def list_usage_limit_policies_for_boundary(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
) -> list[UsageLimitPolicyPublic]:
    """List configured policies contained by one exact executable boundary."""

    from src.models.orm.ai_usage import UsageLimitPolicyORM

    if organization_id is None:
        condition = UsageLimitPolicyORM.scope == UsageLimitScope.PLATFORM.value
    else:
        condition = UsageLimitPolicyORM.organization_id == organization_id
    result = await session.execute(
        select(UsageLimitPolicyORM)
        .where(condition)
        .order_by(UsageLimitPolicyORM.scope, UsageLimitPolicyORM.scope_key)
    )
    return [_policy_to_public(row) for row in result.scalars().all()]


async def upsert_usage_limit_policy(
    session: AsyncSession,
    *,
    scope: UsageLimitScope,
    subject: UsageLimitSubject,
    per_run: UsageCeilings,
    aggregate: UsageCeilings,
    aggregate_period: UsageLimitPeriod,
) -> UsageLimitPolicyPublic:
    """Create or replace one usage-limit policy row."""

    if not per_run.has_any() and not aggregate.has_any():
        raise ValueError("At least one usage ceiling is required")

    from src.models.orm.ai_usage import UsageLimitPolicyORM

    organization_id, user_id, solution_id = _scope_ids(scope, subject)
    scope_key = usage_scope_key(
        scope,
        organization_id=subject.organization_id,
        user_id=subject.user_id,
        solution_id=subject.solution_id,
    )
    existing = (
        await session.execute(
            select(UsageLimitPolicyORM).where(
                UsageLimitPolicyORM.scope == scope.value,
                UsageLimitPolicyORM.scope_key == scope_key,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is None:
        existing = UsageLimitPolicyORM(
            scope=scope.value,
            scope_key=scope_key,
            organization_id=organization_id,
            user_id=user_id,
            solution_id=solution_id,
            created_at=now,
        )
        session.add(existing)
    existing.per_run_ceilings = per_run.configured()
    existing.aggregate_ceilings = aggregate.configured()
    existing.aggregate_period = aggregate_period.value
    existing.updated_at = now
    await session.flush()
    await session.refresh(existing)
    return _policy_to_public(existing)


async def delete_usage_limit_policy(
    session: AsyncSession,
    *,
    scope: UsageLimitScope,
    subject: UsageLimitSubject,
) -> bool:
    """Delete one usage-limit policy row if present."""

    from src.models.orm.ai_usage import UsageLimitPolicyORM

    scope_key = usage_scope_key(
        scope,
        organization_id=subject.organization_id,
        user_id=subject.user_id,
        solution_id=subject.solution_id,
    )
    existing = (
        await session.execute(
            select(UsageLimitPolicyORM).where(
                UsageLimitPolicyORM.scope == scope.value,
                UsageLimitPolicyORM.scope_key == scope_key,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True


async def read_effective_usage_limits(
    session: AsyncSession,
    *,
    subject_scope: UsageLimitScope,
    subject: UsageLimitSubject,
    at: datetime | None = None,
) -> UsageLimitEffectiveResponse:
    """Return effective per-run winner and cumulative aggregate diagnostics."""

    policies = await load_usage_limit_policies(session, subject)
    per_run_scope, per_run = resolve_effective_per_run_limit(policies)
    aggregate_statuses: list[UsageLimitAggregateStatus] = []
    for policy in policies:
        if not policy.aggregate.has_any():
            continue
        period_usage = await load_period_usage(
            session,
            subject,
            period=policy.aggregate_period,
            at=at,
        )
        usage = period_usage.get(policy.scope, PortableUsage())
        dimensions: list[UsageLimitDimensionStatus] = []
        for dimension, limit in policy.aggregate.configured().items():
            current = usage.value_for(dimension)
            dimensions.append(
                UsageLimitDimensionStatus(
                    dimension=dimension,
                    limit=limit,
                    current=current,
                    remaining=max(0, limit - current),
                    percentage=(current / limit * 100.0) if limit else 100.0,
                )
            )
        aggregate_statuses.append(
            UsageLimitAggregateStatus(
                scope=policy.scope.value,
                aggregate_period=policy.aggregate_period.value,
                period_start=usage_period_start(policy.aggregate_period, at),
                usage=_usage_to_dto(usage),
                ceilings=_ceilings_to_dto(policy.aggregate),
                dimensions=dimensions,
            )
        )
    return UsageLimitEffectiveResponse(
        subject_scope=subject_scope.value,
        organization_id=subject.organization_id,
        user_id=subject.user_id,
        solution_id=subject.solution_id,
        effective_per_run_scope=per_run_scope.value if per_run_scope else None,
        effective_per_run=_ceilings_to_dto(per_run),
        aggregate=aggregate_statuses,
    )


async def load_period_usage(
    session: AsyncSession,
    subject: UsageLimitSubject,
    *,
    period: UsageLimitPeriod,
    at: datetime | None = None,
) -> dict[UsageLimitScope, PortableUsage]:
    """Load aggregate usage for each relevant subject scope and period."""

    from src.models.orm.ai_usage import UsageLedgerPeriod

    period_start = usage_period_start(period, at)
    conditions = [
        (UsageLedgerPeriod.scope == scope.value)
        & (
            UsageLedgerPeriod.scope_key
            == usage_scope_key(
                scope,
                organization_id=subject.organization_id,
                user_id=subject.user_id,
                solution_id=subject.solution_id,
            )
        )
        for scope in _scopes_for_subject(subject)
    ]
    if not conditions:
        return {}
    result = await session.execute(
        select(UsageLedgerPeriod).where(
            UsageLedgerPeriod.period == period.value,
            UsageLedgerPeriod.period_start == period_start,
            or_(*conditions),
        )
    )
    rows = result.scalars().all()
    return {UsageLimitScope(row.scope): _usage_from_row(row) for row in rows}


async def record_period_usage(
    session: AsyncSession,
    subject: UsageLimitSubject,
    usage: PortableUsage,
    *,
    period: UsageLimitPeriod,
    at: datetime | None = None,
) -> None:
    """Increment portable usage in every applicable aggregate period bucket."""

    from src.models.orm.ai_usage import UsageLedgerPeriod

    period_start = usage_period_start(period, at)
    for scope in _scopes_for_subject(subject):
        organization_id, user_id, solution_id = _scope_ids(scope, subject)
        scope_key = usage_scope_key(
            scope,
            organization_id=subject.organization_id,
            user_id=subject.user_id,
            solution_id=subject.solution_id,
        )
        now = datetime.now(UTC)
        await session.execute(
            _build_ledger_upsert_statement(
                UsageLedgerPeriod,
                period=period,
                period_start=period_start,
                scope=scope,
                scope_key=scope_key,
                organization_id=organization_id,
                user_id=user_id,
                solution_id=solution_id,
                usage=usage,
                updated_at=now,
            )
        )


async def record_supported_period_usage(
    session: AsyncSession,
    subject: UsageLimitSubject,
    usage: PortableUsage,
    *,
    at: datetime | None = None,
) -> None:
    """Record usage into every supported bucket to avoid later backfills."""

    for period in UsageLimitPeriod:
        await record_period_usage(session, subject, usage, period=period, at=at)


async def evaluate_persisted_usage_limits(
    session: AsyncSession,
    subject: UsageLimitSubject,
    *,
    current_per_run: PortableUsage,
    requested: PortableUsage,
    at: datetime | None = None,
) -> UsageLimitDecision:
    """Load policies and period ledgers, then evaluate the proposed usage."""

    policies = await load_usage_limit_policies(session, subject)
    aggregate_usage: dict[tuple[UsageLimitScope, UsageLimitPeriod], PortableUsage] = {}
    for period in {policy.aggregate_period for policy in policies}:
        for scope, usage in (
            await load_period_usage(session, subject, period=period, at=at)
        ).items():
            aggregate_usage[(scope, period)] = usage
    return evaluate_usage_limits(
        policies=policies,
        current_per_run=current_per_run,
        requested=requested,
        aggregate_usage_by_scope_period=aggregate_usage,
    )


__all__ = [
    "PortableUsage",
    "SCOPE_PRECEDENCE",
    "USAGE_DIMENSIONS",
    "UsageCeilings",
    "UsageLimitDecision",
    "UsageLimitPeriod",
    "UsageLimitPolicy",
    "UsageLimitScope",
    "UsageLimitSubject",
    "UsageLimitViolation",
    "evaluate_persisted_usage_limits",
    "evaluate_usage_limits",
    "load_period_usage",
    "load_usage_limit_policies",
    "record_period_usage",
    "record_supported_period_usage",
    "resolve_effective_per_run_limit",
    "usage_day",
    "usage_period_start",
    "usage_scope_key",
]
