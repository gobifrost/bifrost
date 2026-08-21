"""Provider-neutral hierarchical usage-limit evaluation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from src.models.orm.ai_usage import UsageLedgerPeriod, UsageLimitPolicyORM
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.usage_limits import (
    PortableUsage,
    UsageCeilings,
    UsageLimitPolicy,
    UsageLimitPeriod,
    UsageLimitScope,
    UsageLimitSubject,
    evaluate_persisted_usage_limits,
    evaluate_usage_limits,
    load_period_usage,
    load_usage_limit_policies,
    read_effective_usage_limits,
    record_supported_period_usage,
    resolve_effective_per_run_limit,
    upsert_usage_limit_policy,
    usage_subject_for_scope,
)
from src.models.contracts.ai_usage import UsageLimitPolicyUpsert
from src.services.usage_limits import _build_ledger_upsert_statement
from src.services.ai_usage_service import _portable_usage_for_ai_model_call


def test_solution_per_run_limit_wins_over_user_org_and_platform_defaults() -> None:
    policies = [
        UsageLimitPolicy(
            scope=UsageLimitScope.PLATFORM,
            per_run=UsageCeilings(total_tokens=100_000),
        ),
        UsageLimitPolicy(
            scope=UsageLimitScope.ORGANIZATION,
            per_run=UsageCeilings(total_tokens=50_000),
        ),
        UsageLimitPolicy(
            scope=UsageLimitScope.USER,
            per_run=UsageCeilings(total_tokens=25_000),
        ),
        UsageLimitPolicy(
            scope=UsageLimitScope.SOLUTION,
            per_run=UsageCeilings(total_tokens=10_000),
        ),
    ]

    scope, limit = resolve_effective_per_run_limit(policies)

    assert scope == UsageLimitScope.SOLUTION
    assert limit.total_tokens == 10_000


def test_per_run_limit_uses_next_most_specific_configured_scope() -> None:
    scope, limit = resolve_effective_per_run_limit(
        [
            UsageLimitPolicy(
                scope=UsageLimitScope.PLATFORM,
                per_run=UsageCeilings(total_tokens=100_000),
            ),
            UsageLimitPolicy(
                scope=UsageLimitScope.USER,
                per_run=UsageCeilings(model_requests=20),
            ),
        ]
    )

    assert scope == UsageLimitScope.USER
    assert limit.model_requests == 20
    assert limit.total_tokens is None


def test_parent_aggregate_ceiling_still_constrains_more_specific_run() -> None:
    decision = evaluate_usage_limits(
        policies=[
            UsageLimitPolicy(
                scope=UsageLimitScope.PLATFORM,
                aggregate=UsageCeilings(total_tokens=1_000),
            ),
            UsageLimitPolicy(
                scope=UsageLimitScope.SOLUTION,
                per_run=UsageCeilings(total_tokens=10_000),
            ),
        ],
        current_per_run=PortableUsage(input_tokens=100),
        requested=PortableUsage(input_tokens=100, output_tokens=50),
        aggregate_usage_by_scope_period={
            (UsageLimitScope.PLATFORM, UsageLimitPeriod.MONTHLY): PortableUsage(
                input_tokens=900
            ),
        },
    )

    assert decision.allowed is False
    assert decision.per_run_scope == UsageLimitScope.SOLUTION
    assert [(v.scope, v.kind, v.dimension) for v in decision.violations] == [
        (UsageLimitScope.PLATFORM, "aggregate", "total_tokens")
    ]
    assert decision.violations[0].projected == 1_050


def test_total_tokens_does_not_double_count_cache_breakdowns() -> None:
    usage = PortableUsage(
        input_tokens=100,
        output_tokens=25,
        cache_read_tokens=80,
        cache_write_tokens=10,
    )

    assert usage.total_tokens == 125


def test_ai_model_call_usage_does_not_record_runner_duration() -> None:
    first = _portable_usage_for_ai_model_call(
        input_tokens=100,
        output_tokens=25,
        cache_read_tokens=50,
        cache_write_tokens=10,
    )
    second = _portable_usage_for_ai_model_call(
        input_tokens=200,
        output_tokens=50,
        cache_read_tokens=100,
        cache_write_tokens=20,
    )

    combined = first + second

    assert combined.model_requests == 2
    assert combined.runner_duration_ms == 0
    assert combined.sandbox_compute_ms == 0


def test_ai_model_call_usage_preserves_turn_total_model_request_count() -> None:
    usage = _portable_usage_for_ai_model_call(
        input_tokens=300,
        output_tokens=75,
        cache_read_tokens=40,
        cache_write_tokens=5,
        model_requests=3,
    )

    assert usage.model_requests == 3
    assert usage.total_tokens == 375


def test_provider_neutral_dimensions_include_cache_and_runtime_usage() -> None:
    decision = evaluate_usage_limits(
        policies=[
            UsageLimitPolicy(
                scope=UsageLimitScope.ORGANIZATION,
                per_run=UsageCeilings(
                    cache_read_tokens=200,
                    runner_duration_ms=1_000,
                    sandbox_compute_ms=2_000,
                ),
            )
        ],
        current_per_run=PortableUsage(
            cache_read_tokens=150,
            runner_duration_ms=700,
            sandbox_compute_ms=1_500,
        ),
        requested=PortableUsage(
            cache_read_tokens=75,
            runner_duration_ms=200,
            sandbox_compute_ms=600,
        ),
        aggregate_usage_by_scope_period={},
    )

    assert decision.allowed is False
    assert {
        (violation.kind, violation.dimension, violation.projected)
        for violation in decision.violations
    } == {
        ("per_run", "cache_read_tokens", 225),
        ("per_run", "sandbox_compute_ms", 2_100),
    }


def test_allowed_when_specific_run_and_all_aggregate_scopes_have_headroom() -> None:
    decision = evaluate_usage_limits(
        policies=[
            UsageLimitPolicy(
                scope=UsageLimitScope.PLATFORM,
                aggregate=UsageCeilings(model_requests=100),
            ),
            UsageLimitPolicy(
                scope=UsageLimitScope.ORGANIZATION,
                aggregate=UsageCeilings(model_requests=50),
            ),
            UsageLimitPolicy(
                scope=UsageLimitScope.USER,
                per_run=UsageCeilings(model_requests=5),
                aggregate=UsageCeilings(model_requests=10),
            ),
        ],
        current_per_run=PortableUsage(model_requests=2),
        requested=PortableUsage(model_requests=2),
        aggregate_usage_by_scope_period={
            (UsageLimitScope.PLATFORM, UsageLimitPeriod.MONTHLY): PortableUsage(
                model_requests=90
            ),
            (UsageLimitScope.ORGANIZATION, UsageLimitPeriod.MONTHLY): PortableUsage(
                model_requests=40
            ),
            (UsageLimitScope.USER, UsageLimitPeriod.MONTHLY): PortableUsage(
                model_requests=7
            ),
        },
    )

    assert decision.allowed is True
    assert decision.per_run_scope == UsageLimitScope.USER
    assert decision.violations == ()


@pytest.mark.asyncio
async def test_loads_policies_from_durable_scope_keys(db_session) -> None:
    organization = Organization(
        name="Usage Org",
        domain="usage.example",
        created_by="test",
    )
    user = User(
        email=f"usage-{uuid4()}@example.com",
        organization=organization,
    )
    db_session.add_all([organization, user])
    await db_session.flush()
    solution = Solution(
        slug=f"usage-{uuid4()}",
        name="Usage Solution",
        organization_id=organization.id,
        owner_user_id=user.id,
        visibility="private",
    )
    db_session.add(solution)
    await db_session.flush()

    db_session.add_all(
        [
            UsageLimitPolicyORM(
                scope="platform",
                scope_key="platform",
                aggregate_ceilings={"total_tokens": 10_000},
                aggregate_period="monthly",
            ),
            UsageLimitPolicyORM(
                scope="organization",
                scope_key=str(organization.id),
                organization_id=organization.id,
                per_run_ceilings={"model_requests": 10},
            ),
            UsageLimitPolicyORM(
                scope="solution",
                scope_key=str(solution.id),
                organization_id=organization.id,
                user_id=user.id,
                solution_id=solution.id,
                per_run_ceilings={"total_tokens": 500},
            ),
        ]
    )
    await db_session.flush()

    policies = await load_usage_limit_policies(
        db_session,
        UsageLimitSubject(
            organization_id=organization.id,
            user_id=user.id,
            solution_id=solution.id,
        ),
    )

    by_scope = {policy.scope: policy for policy in policies}
    assert by_scope[UsageLimitScope.PLATFORM].aggregate.total_tokens == 10_000
    assert by_scope[UsageLimitScope.PLATFORM].aggregate_period == UsageLimitPeriod.MONTHLY
    assert by_scope[UsageLimitScope.ORGANIZATION].per_run.model_requests == 10
    assert by_scope[UsageLimitScope.SOLUTION].per_run.total_tokens == 500


@pytest.mark.asyncio
async def test_records_supported_period_usage_for_every_applicable_scope(
    db_session,
) -> None:
    organization = Organization(
        name="Ledger Org",
        domain="ledger.example",
        created_by="test",
    )
    user = User(
        email=f"ledger-{uuid4()}@example.com",
        organization=organization,
    )
    db_session.add_all([organization, user])
    await db_session.flush()
    solution = Solution(
        slug=f"ledger-{uuid4()}",
        name="Ledger Solution",
        organization_id=organization.id,
        owner_user_id=user.id,
        visibility="private",
    )
    db_session.add(solution)
    await db_session.flush()

    subject = UsageLimitSubject(
        organization_id=organization.id,
        user_id=user.id,
        solution_id=solution.id,
    )
    timestamp = datetime(2026, 8, 20, 16, 30, tzinfo=UTC)
    await record_supported_period_usage(
        db_session,
        subject,
        PortableUsage(
            model_requests=1,
            input_tokens=100,
            output_tokens=25,
            cache_read_tokens=80,
            cache_write_tokens=10,
            runner_duration_ms=250,
            sandbox_compute_ms=750,
        ),
        at=timestamp,
    )
    await record_supported_period_usage(
        db_session,
        subject,
        PortableUsage(model_requests=2, input_tokens=50, output_tokens=5),
        at=timestamp,
    )
    await db_session.flush()

    daily_usage = await load_period_usage(
        db_session,
        subject,
        period=UsageLimitPeriod.DAILY,
        at=timestamp,
    )
    monthly_usage = await load_period_usage(
        db_session,
        subject,
        period=UsageLimitPeriod.MONTHLY,
        at=timestamp,
    )

    assert set(daily_usage) == {
        UsageLimitScope.PLATFORM,
        UsageLimitScope.ORGANIZATION,
        UsageLimitScope.USER,
        UsageLimitScope.SOLUTION,
    }
    assert daily_usage[UsageLimitScope.PLATFORM].model_requests == 3
    assert daily_usage[UsageLimitScope.SOLUTION].input_tokens == 150
    assert daily_usage[UsageLimitScope.SOLUTION].total_tokens == 180
    assert daily_usage[UsageLimitScope.SOLUTION].cache_read_tokens == 80
    assert daily_usage[UsageLimitScope.SOLUTION].runner_duration_ms == 250
    assert monthly_usage[UsageLimitScope.SOLUTION].input_tokens == 150

    rows = (
        await db_session.execute(
            select(UsageLedgerPeriod).where(
                UsageLedgerPeriod.period_start.in_(
                    [date(2026, 8, 20), date(2026, 8, 1)]
                )
            )
        )
    ).scalars().all()
    assert len(rows) == 8


@pytest.mark.asyncio
async def test_evaluates_mixed_monthly_and_daily_policy_periods(db_session) -> None:
    user_id = uuid4()
    organization = Organization(
        name="Mixed Period Org",
        domain="mixed-period.example",
        created_by="test",
    )
    user = User(
        id=user_id,
        email=f"mixed-{user_id}@example.com",
        organization=organization,
    )
    db_session.add_all([organization, user])
    await db_session.flush()
    db_session.add_all(
        [
            UsageLimitPolicyORM(
                scope="platform",
                scope_key="platform",
                aggregate_ceilings={"model_requests": 10},
                aggregate_period="monthly",
            ),
            UsageLimitPolicyORM(
                scope="user",
                scope_key=str(user_id),
                user_id=user_id,
                aggregate_ceilings={"model_requests": 2},
                aggregate_period="daily",
            ),
        ]
    )
    await db_session.flush()
    subject = UsageLimitSubject(
        organization_id=organization.id,
        user_id=user_id,
    )
    timestamp = datetime(2026, 8, 20, 12, tzinfo=UTC)
    await record_supported_period_usage(
        db_session,
        subject,
        PortableUsage(model_requests=8),
        at=timestamp,
    )

    decision = await evaluate_persisted_usage_limits(
        db_session,
        subject,
        current_per_run=PortableUsage(),
        requested=PortableUsage(model_requests=1),
        at=timestamp,
    )

    assert decision.allowed is False
    assert [(v.scope, v.kind, v.dimension, v.projected) for v in decision.violations] == [
        (UsageLimitScope.USER, "aggregate", "model_requests", 9)
    ]


def test_ledger_upsert_is_atomic_additive_postgresql_statement() -> None:
    statement = _build_ledger_upsert_statement(
        UsageLedgerPeriod,
        period=UsageLimitPeriod.MONTHLY,
        period_start=date(2026, 8, 1),
        scope=UsageLimitScope.PLATFORM,
        scope_key="platform",
        organization_id=None,
        user_id=None,
        solution_id=None,
        usage=PortableUsage(model_requests=2, input_tokens=100),
        updated_at=datetime(2026, 8, 20, tzinfo=UTC),
    )

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "ON CONFLICT (period, period_start, scope, scope_key) DO UPDATE" in compiled
    assert (
        "model_requests = (usage_ledger_periods.model_requests + "
        "excluded.model_requests)"
    ) in compiled
    assert (
        "input_tokens = (usage_ledger_periods.input_tokens + "
        "excluded.input_tokens)"
    ) in compiled


@pytest.mark.asyncio
async def test_policy_scope_target_shape_is_database_enforced(db_session) -> None:
    db_session.add(
        UsageLimitPolicyORM(
            scope="platform",
            scope_key="not-platform",
            aggregate_ceilings={"model_requests": 1},
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


def test_usage_limit_policy_upsert_rejects_empty_policy() -> None:
    with pytest.raises(ValueError, match="At least one"):
        UsageLimitPolicyUpsert()


@pytest.mark.asyncio
async def test_effective_usage_limit_read_model_reports_percentages(db_session) -> None:
    org = Organization(
        name=f"Usage Org {uuid4()}",
        domain=f"{uuid4().hex}.test",
        created_by="usage-limit-test",
    )
    user = User(
        email=f"{uuid4().hex}@example.com",
        name="Usage User",
        organization=org,
    )
    db_session.add_all([org, user])
    await db_session.flush()
    solution = Solution(
        slug=f"usage-{uuid4().hex}",
        name="Usage Solution",
        organization_id=org.id,
    )
    db_session.add(solution)
    await db_session.flush()
    subject = usage_subject_for_scope(
        UsageLimitScope.SOLUTION,
        organization_id=org.id,
        user_id=user.id,
        solution_id=solution.id,
    )
    await upsert_usage_limit_policy(
        db_session,
        scope=UsageLimitScope.PLATFORM,
        subject=subject,
        per_run=UsageCeilings(total_tokens=100),
        aggregate=UsageCeilings(model_requests=10),
        aggregate_period=UsageLimitPeriod.MONTHLY,
    )
    await upsert_usage_limit_policy(
        db_session,
        scope=UsageLimitScope.SOLUTION,
        subject=subject,
        per_run=UsageCeilings(total_tokens=1_000),
        aggregate=UsageCeilings(output_tokens=200),
        aggregate_period=UsageLimitPeriod.MONTHLY,
    )
    timestamp = datetime(2026, 8, 20, tzinfo=UTC)
    await record_supported_period_usage(
        db_session,
        subject,
        PortableUsage(model_requests=3, output_tokens=50),
        at=timestamp,
    )

    result = await read_effective_usage_limits(
        db_session,
        subject_scope=UsageLimitScope.SOLUTION,
        subject=subject,
        at=timestamp,
    )

    assert result.effective_per_run_scope == "solution"
    assert result.effective_per_run.total_tokens == 1_000
    by_scope = {item.scope: item for item in result.aggregate}
    assert by_scope["platform"].dimensions[0].percentage == 30.0
    assert by_scope["solution"].dimensions[0].current == 50
    assert by_scope["solution"].dimensions[0].remaining == 150
