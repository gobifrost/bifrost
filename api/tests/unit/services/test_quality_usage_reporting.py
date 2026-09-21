from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

import shared.quality_usage_reporting as reporting
from shared.quality_usage_reporting import (
    RUNTIME_AGENT,
    SIMULATION,
    TEST_DESIGNER,
    QualityUsageReportFilters,
    UsageReportPagination,
    summarize_quality_operation_usage,
    summarize_quality_usage,
    utc_inclusive_dates_to_half_open,
)
from src.core import db_deps
from src.core.auth import ExecutionContext
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.executions import Execution
from src.models.orm.metrics import KnowledgeStorageDaily
from src.models.orm.organizations import Organization

pytestmark = pytest.mark.asyncio


def _dt(minutes: int = 0) -> datetime:
    return datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc) + timedelta(
        minutes=minutes
    )


async def _seed_org(db_session, *, name: str) -> Organization:
    org = Organization(id=uuid4(), name=name, created_by="test@example.com")
    db_session.add(org)
    await db_session.flush()
    return org


async def _seed_agent_run(
    db_session,
    *,
    org_id,
    agent_id,
    trigger_type="api",
    root_run_id=None,
    parent_run_id=None,
    correlation=None,
    execution_snapshot=None,
) -> AgentRun:
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        agent_id=agent_id,
        org_id=org_id,
        trigger_type=trigger_type,
        status="completed",
        root_run_id=root_run_id or run_id,
        parent_run_id=parent_run_id,
        correlation=correlation,
        execution_snapshot=execution_snapshot,
    )
    db_session.add(run)
    await db_session.flush()
    return run


def _usage(**overrides) -> AIUsage:
    values = {
        "provider": "openai",
        "model": "gpt-test",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "provider_cost": None,
        "cost": Decimal("0.01000000"),
        "duration_ms": 100,
        "timestamp": _dt(),
        "sequence": 1,
    }
    values.update(overrides)
    return AIUsage(**values)


def _attempt(**overrides) -> AIUsageAttempt:
    values = {
        "idempotency_key": f"attempt-{uuid4()}",
        "quality_operation_type": "recorded_evaluation",
        "quality_operation_id": uuid4(),
        "usage_purpose": "recorded_semantic_judge",
        "provider": "openai",
        "model": "gpt-test",
        "request_fingerprint": f"fp-{uuid4()}",
        "state": "started",
        "started_at": _dt(),
    }
    values.update(overrides)
    return AIUsageAttempt(**values)


async def test_global_aggregates_conserve_usage_and_attempt_gaps(
    db_session, seed_agent
):
    org_a = await _seed_org(db_session, name="Org A")
    org_b = await _seed_org(db_session, name="Org B")
    recorded_op = uuid4()
    other_recorded_op = uuid4()

    observed_attempt = _attempt(
        organization_id=org_a.id,
        quality_operation_id=recorded_op,
        state="observed",
        observed_at=_dt(3),
        profile_id=uuid4(),
        profile_name="Judge Profile",
        profile_fingerprint="judge-fp",
    )
    started_gap = _attempt(
        organization_id=org_a.id,
        quality_operation_id=recorded_op,
        state="started",
        profile_fingerprint="judge-fp",
    )
    unobserved_gap = _attempt(
        organization_id=org_a.id,
        quality_operation_id=recorded_op,
        state="unobserved",
        unobserved_reason="missing_usage",
        profile_fingerprint="judge-fp",
    )
    other_tenant_attempt = _attempt(
        organization_id=org_b.id,
        quality_operation_id=other_recorded_op,
        state="started",
    )
    db_session.add_all(
        [observed_attempt, started_gap, unobserved_gap, other_tenant_attempt]
    )
    await db_session.flush()

    quality_provider_cost = _usage(
        organization_id=org_a.id,
        provider="openai",
        model="gpt-quality",
        input_tokens=100,
        output_tokens=40,
        cache_read_tokens=10,
        cache_write_tokens=3,
        provider_cost=Decimal("0.12000000"),
        cost=Decimal("0.12000000"),
        quality_operation_type="recorded_evaluation",
        quality_operation_id=recorded_op,
        usage_purpose="recorded_semantic_judge",
        profile_id=observed_attempt.profile_id,
        profile_name="Judge Profile",
        profile_fingerprint="judge-fp",
        usage_attempt_id=observed_attempt.id,
        timestamp=observed_attempt.started_at,
    )
    quality_estimated = _usage(
        organization_id=org_a.id,
        provider="anthropic",
        model="claude-quality",
        input_tokens=30,
        output_tokens=20,
        provider_cost=None,
        cost=Decimal("0.05000000"),
        quality_operation_type="recorded_evaluation",
        quality_operation_id=recorded_op,
        usage_purpose="recorded_semantic_judge",
        timestamp=_dt(1),
    )
    quality_missing_cost = _usage(
        organization_id=org_a.id,
        provider="openai",
        model="gpt-quality",
        input_tokens=5,
        output_tokens=2,
        provider_cost=None,
        cost=None,
        quality_operation_type="recorded_evaluation",
        quality_operation_id=recorded_op,
        usage_purpose="recorded_semantic_judge",
        duration_ms=None,
        timestamp=_dt(2),
    )
    other_tenant_usage = _usage(
        organization_id=org_b.id,
        quality_operation_type="recorded_evaluation",
        quality_operation_id=other_recorded_op,
        usage_purpose="recorded_semantic_judge",
    )
    db_session.add_all(
        [
            quality_provider_cost,
            quality_estimated,
            quality_missing_cost,
            other_tenant_usage,
        ]
    )
    await db_session.flush()

    report = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(scope="organization", organization_id=org_a.id),
    )

    assert report["overall"]["input_tokens"] == 135
    assert report["overall"]["output_tokens"] == 62
    assert report["overall"]["cache_read_tokens"] == 10
    assert report["overall"]["cache_write_tokens"] == 3
    assert report["overall"]["call_count"] == 3
    assert report["overall"]["observed_provider_cost"] == Decimal("0.12000000")
    assert report["overall"]["estimated_cost"] == Decimal("0.05000000")
    assert report["overall"]["known_cost"] == Decimal("0.17000000")
    assert report["overall"]["missing_cost_call_count"] == 1
    assert report["overall"]["duration_missing_count"] == 1
    assert report["coverage"]["started_attempt_count"] == 1
    assert report["coverage"]["unobserved_attempt_count"] == 1
    assert report["coverage"]["legacy_coverage_unknown"] is True

    purpose = report["by_purpose"]["items"][0]
    assert purpose["purpose"] == "recorded_semantic_judge"
    assert purpose["totals"]["known_cost"] == Decimal("0.17000000")
    assert purpose["coverage"]["started_attempt_count"] == 1
    assert purpose["coverage"]["unobserved_attempt_count"] == 1

    providers = {
        (row["provider"], row["model"]): row
        for row in report["by_provider_model"]["items"]
    }
    assert (
        providers[("openai", "gpt-quality")]["totals"]["missing_cost_call_count"] == 1
    )
    assert (
        providers[("openai", "gpt-quality")]["coverage"]["missing_cost_call_count"] == 1
    )
    assert providers[("anthropic", "claude-quality")]["totals"][
        "estimated_cost"
    ] == Decimal("0.05000000")

    orgs = report["by_organization"]["items"]
    assert len(orgs) == 1
    assert orgs[0]["organization_id"] == org_a.id
    assert orgs[0]["organization_name"] == "Org A"


async def test_source_filter_preserves_agent_semantics_and_does_not_rebook_source_runs(
    db_session, seed_agent
):
    org = await _seed_org(db_session, name="Org")
    production = await _seed_agent_run(
        db_session, org_id=org.id, agent_id=seed_agent.id
    )
    quality_op = uuid4()
    db_session.add_all(
        [
            _usage(
                organization_id=org.id,
                agent_run_id=production.id,
                input_tokens=200,
                output_tokens=50,
                cost=Decimal("0.20000000"),
                provider_cost=Decimal("0.20000000"),
            ),
            _usage(
                organization_id=org.id,
                input_tokens=10,
                output_tokens=3,
                cost=Decimal("0.03000000"),
                provider_cost=Decimal("0.03000000"),
                quality_operation_type="recorded_evaluation",
                quality_operation_id=quality_op,
                usage_purpose="recorded_semantic_judge",
            ),
            _attempt(
                organization_id=org.id,
                quality_operation_id=quality_op,
                state="started",
            ),
        ]
    )
    await db_session.flush()

    all_report = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(
            scope="organization", organization_id=org.id, source="all"
        ),
    )
    agent_report = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(
            scope="organization", organization_id=org.id, source="agents"
        ),
    )

    assert all_report["overall"]["known_cost"] == Decimal("0.23000000")
    assert all_report["coverage"]["started_attempt_count"] == 1
    assert agent_report["overall"]["known_cost"] == Decimal("0.20000000")
    assert agent_report["coverage"]["started_attempt_count"] == 0
    assert agent_report["by_purpose"]["items"][0]["purpose"] == RUNTIME_AGENT


async def test_synthetic_simulation_designer_descendants_and_missing_lineage(
    db_session, seed_agent
):
    org = await _seed_org(db_session, name="Org")
    execution_id = uuid4()
    sim_parent = await _seed_agent_run(
        db_session,
        org_id=org.id,
        agent_id=seed_agent.id,
        trigger_type="evaluation_synthetic",
        correlation={
            "evaluation_mode": "evaluation_synthetic",
            "evaluation_execution_id": str(execution_id),
        },
    )
    sim_child = await _seed_agent_run(
        db_session,
        org_id=org.id,
        agent_id=seed_agent.id,
        trigger_type="evaluation_synthetic",
        root_run_id=sim_parent.id,
        parent_run_id=sim_parent.id,
        correlation={
            "evaluation_mode": "evaluation_synthetic",
            "evaluation_execution_id": str(execution_id),
        },
    )
    designer = await _seed_agent_run(
        db_session,
        org_id=org.id,
        agent_id=seed_agent.id,
        trigger_type="evaluation_synthetic",
        correlation={
            "evaluation_mode": "evaluation_synthetic",
            "evaluation_designer": True,
            "evaluation_execution_id": str(uuid4()),
        },
    )
    malformed = await _seed_agent_run(
        db_session,
        org_id=org.id,
        agent_id=seed_agent.id,
        trigger_type="evaluation_synthetic",
        correlation={
            "evaluation_mode": "evaluation_synthetic",
            "evaluation_execution_id": "not-a-uuid",
            "evaluation_designer": "true",
        },
    )
    production_with_correlation = await _seed_agent_run(
        db_session,
        org_id=org.id,
        agent_id=seed_agent.id,
        trigger_type="api",
        correlation={
            "evaluation_mode": "evaluation_synthetic",
            "evaluation_execution_id": str(uuid4()),
        },
    )
    db_session.add_all(
        [
            _usage(
                organization_id=org.id,
                agent_run_id=sim_parent.id,
                input_tokens=10,
                output_tokens=1,
                cost=Decimal("0.01000000"),
            ),
            _usage(
                organization_id=org.id,
                agent_run_id=sim_child.id,
                input_tokens=20,
                output_tokens=2,
                cost=Decimal("0.02000000"),
            ),
            _usage(
                organization_id=org.id,
                agent_run_id=designer.id,
                input_tokens=30,
                output_tokens=3,
                cost=Decimal("0.03000000"),
            ),
            _usage(
                organization_id=org.id,
                agent_run_id=malformed.id,
                input_tokens=40,
                output_tokens=4,
                cost=Decimal("0.04000000"),
            ),
            _usage(
                organization_id=org.id,
                agent_run_id=production_with_correlation.id,
                input_tokens=50,
                output_tokens=5,
                cost=Decimal("0.05000000"),
            ),
        ]
    )
    await db_session.flush()

    report = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(scope="organization", organization_id=org.id),
    )
    purposes = {row["purpose"]: row for row in report["by_purpose"]["items"]}

    assert purposes[SIMULATION]["totals"]["input_tokens"] == 70
    assert purposes[SIMULATION]["totals"]["call_count"] == 3
    assert purposes[TEST_DESIGNER]["totals"]["input_tokens"] == 30
    assert purposes[RUNTIME_AGENT]["totals"]["input_tokens"] == 50
    assert report["coverage"]["unassigned_operation_call_count"] == 1

    op_report = await summarize_quality_operation_usage(
        db_session,
        scope="organization",
        organization_id=org.id,
        operation_type="synthetic_evaluation",
        operation_id=execution_id,
    )
    assert op_report["overall"]["input_tokens"] == 30
    assert op_report["overall"]["call_count"] == 2

    designer_report = await summarize_quality_operation_usage(
        db_session,
        scope="organization",
        organization_id=org.id,
        operation_type="quality_designer",
        operation_id=designer.id,
    )
    assert designer_report["overall"]["input_tokens"] == 30
    assert designer_report["by_purpose"]["items"][0]["purpose"] == TEST_DESIGNER


async def test_profile_date_provider_operation_filters_and_pagination(
    db_session, seed_agent
):
    org = await _seed_org(db_session, name="Org")
    profile_id = uuid4()
    runtime_profile_id = uuid4()
    operation_id = uuid4()
    db_session.add_all(
        [
            _usage(
                organization_id=org.id,
                provider="openai",
                model="a-model",
                input_tokens=10,
                output_tokens=1,
                profile_id=profile_id,
                profile_name="Quality Profile",
                profile_fingerprint="quality-fp",
                quality_operation_type="recorded_evaluation",
                quality_operation_id=operation_id,
                usage_purpose="recorded_semantic_judge",
                timestamp=_dt(0),
            ),
            _usage(
                organization_id=org.id,
                provider="openai",
                model="b-model",
                input_tokens=20,
                output_tokens=2,
                quality_operation_type="recorded_evaluation",
                quality_operation_id=uuid4(),
                usage_purpose="recorded_semantic_judge",
                timestamp=_dt(10),
            ),
        ]
    )
    runtime = await _seed_agent_run(
        db_session,
        org_id=org.id,
        agent_id=seed_agent.id,
        execution_snapshot={"model": {"profile_id": str(runtime_profile_id)}},
    )
    db_session.add_all(
        [
            _usage(
                organization_id=org.id,
                agent_run_id=runtime.id,
                provider="openai",
                model="runtime-model",
                input_tokens=5,
                output_tokens=1,
                timestamp=_dt(1),
                sequence=1,
            ),
            _usage(
                organization_id=org.id,
                agent_run_id=runtime.id,
                provider="openai",
                model="summary-model",
                input_tokens=7,
                output_tokens=1,
                timestamp=_dt(2),
                sequence=0,
            ),
        ]
    )
    await db_session.flush()

    filtered = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(
            scope="organization",
            organization_id=org.id,
            start_at=_dt(0),
            end_at=_dt(5),
            provider="openai",
            model="a-model",
            profile_id=profile_id,
            quality_operation_type="recorded_evaluation",
            quality_operation_id=operation_id,
            pagination=UsageReportPagination(limit=1, offset=0),
        ),
    )
    assert filtered["overall"]["input_tokens"] == 10
    assert filtered["by_provider_model"]["total_groups"] == 1

    runtime_profile = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(
            scope="organization", organization_id=org.id, profile_id=runtime_profile_id
        ),
    )
    assert runtime_profile["overall"]["input_tokens"] == 5
    assert runtime_profile["by_purpose"]["items"][0]["purpose"] == RUNTIME_AGENT

    all_usage = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(scope="organization", organization_id=org.id),
    )
    summary_profile_groups = [
        item
        for item in all_usage["by_profile"]["items"]
        if item["purpose"] == reporting.AGENT_SUMMARY
    ]
    assert len(summary_profile_groups) == 1
    assert summary_profile_groups[0]["profile_id"] is None
    assert summary_profile_groups[0]["model"] == "summary-model"
    assert summary_profile_groups[0]["totals"]["input_tokens"] == 7

    paged = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(
            scope="organization",
            organization_id=org.id,
            pagination=UsageReportPagination(limit=1, offset=0),
        ),
    )
    assert paged["by_provider_model"]["total_groups"] >= 4
    assert len(paged["by_provider_model"]["items"]) == 1
    assert paged["overall"]["input_tokens"] == 42


async def test_filter_validation_requires_explicit_scope_and_complete_operation():
    with pytest.raises(ValueError):
        QualityUsageReportFilters(scope="organization").validate()
    with pytest.raises(ValueError):
        QualityUsageReportFilters(scope="platform", organization_id=uuid4()).validate()
    with pytest.raises(ValueError):
        QualityUsageReportFilters(scope="global_only", organization_id=uuid4()).validate()
    with pytest.raises(ValueError):
        QualityUsageReportFilters(
            scope="platform", quality_operation_type="recorded_evaluation"
        ).validate()
    with pytest.raises(ValueError):
        QualityUsageReportFilters(
            scope="platform", start_at=_dt(1), end_at=_dt(0)
        ).validate()
    with pytest.raises(ValueError):
        QualityUsageReportFilters(
            scope="platform", pagination=UsageReportPagination(limit=201)
        ).validate()
    with pytest.raises(ValueError):
        QualityUsageReportFilters(
            scope="platform", start_at=datetime(2026, 9, 20, 12, 0)
        ).validate()
    with pytest.raises(ValueError):
        QualityUsageReportFilters(
            scope="platform",
            start_at=datetime(2026, 9, 20, 8, 0, tzinfo=timezone(timedelta(hours=-4))),
        ).validate()


async def test_global_only_scope_filters_usage_and_unobserved_attempts(db_session):
    org = await _seed_org(db_session, name="Tenant Org")
    operation_id = uuid4()
    tenant_operation_id = uuid4()
    db_session.add_all(
        [
            _usage(
                organization_id=None,
                input_tokens=12,
                output_tokens=4,
                provider_cost=Decimal("0.00040000"),
                cost=Decimal("0.00040000"),
                quality_operation_type="recorded_evaluation",
                quality_operation_id=operation_id,
                usage_purpose="recorded_semantic_judge",
            ),
            _usage(
                organization_id=org.id,
                input_tokens=100,
                output_tokens=40,
                provider_cost=Decimal("0.00400000"),
                cost=Decimal("0.00400000"),
                quality_operation_type="recorded_evaluation",
                quality_operation_id=tenant_operation_id,
                usage_purpose="recorded_semantic_judge",
            ),
            _attempt(
                organization_id=None,
                quality_operation_id=operation_id,
                state="started",
            ),
            _attempt(
                organization_id=None,
                quality_operation_id=operation_id,
                state="unobserved",
                unobserved_reason="missing_usage",
            ),
            _attempt(
                organization_id=org.id,
                quality_operation_id=tenant_operation_id,
                state="started",
            ),
        ]
    )
    await db_session.flush()

    report = await summarize_quality_usage(
        db_session, QualityUsageReportFilters(scope="global_only")
    )

    assert report["overall"]["input_tokens"] == 12
    assert report["overall"]["known_cost"] == Decimal("0.00040000")
    assert report["coverage"]["started_attempt_count"] == 1
    assert report["coverage"]["unobserved_attempt_count"] == 1
    assert report["by_organization"]["items"][0]["organization_id"] is None


async def test_public_date_window_is_half_open_and_rejects_overflow():
    start_at, end_at = utc_inclusive_dates_to_half_open(
        datetime(2026, 9, 20, tzinfo=timezone.utc).date(),
        datetime(2026, 9, 20, tzinfo=timezone.utc).date(),
    )
    assert start_at == datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    assert end_at == datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="on or after"):
        utc_inclusive_dates_to_half_open(
            datetime(2026, 9, 21, tzinfo=timezone.utc).date(),
            datetime(2026, 9, 20, tzinfo=timezone.utc).date(),
        )

    with pytest.raises(ValueError, match="too large"):
        utc_inclusive_dates_to_half_open(date.max, date.max)


async def test_end_at_is_exclusive_for_usage_and_attempt_gaps(db_session):
    org = await _seed_org(db_session, name="Window Org")
    operation_id = uuid4()
    start_at = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    end_at = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)

    db_session.add_all(
        [
            _usage(
                organization_id=org.id,
                input_tokens=10,
                output_tokens=1,
                timestamp=end_at - timedelta(microseconds=1),
                quality_operation_type="recorded_evaluation",
                quality_operation_id=operation_id,
                usage_purpose="recorded_semantic_judge",
            ),
            _usage(
                organization_id=org.id,
                input_tokens=100,
                output_tokens=10,
                timestamp=end_at,
                quality_operation_type="recorded_evaluation",
                quality_operation_id=operation_id,
                usage_purpose="recorded_semantic_judge",
            ),
            _attempt(
                organization_id=org.id,
                quality_operation_id=operation_id,
                state="started",
                started_at=end_at - timedelta(microseconds=1),
            ),
            _attempt(
                organization_id=org.id,
                quality_operation_id=operation_id,
                state="started",
                started_at=end_at,
            ),
        ]
    )
    await db_session.flush()

    report = await summarize_quality_usage(
        db_session,
        QualityUsageReportFilters(
            scope="organization",
            organization_id=org.id,
            start_at=start_at,
            end_at=end_at,
        ),
    )

    assert report["overall"]["input_tokens"] == 10
    assert report["coverage"]["started_attempt_count"] == 1


async def test_public_usage_breakdown_route_prefers_explicit_org_and_serializes_decimal(
    db_session,
):
    from src.routers.usage_reports import get_usage_breakdown

    ctx_org = await _seed_org(db_session, name="Context Org")
    explicit_org = await _seed_org(db_session, name="Explicit Org")
    db_session.add_all(
        [
            _usage(
                organization_id=ctx_org.id,
                input_tokens=100,
                output_tokens=10,
                timestamp=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
                quality_operation_type="recorded_evaluation",
                quality_operation_id=uuid4(),
                usage_purpose="recorded_semantic_judge",
            ),
            _usage(
                organization_id=explicit_org.id,
                input_tokens=7,
                output_tokens=3,
                provider_cost=Decimal("0.00020000"),
                cost=Decimal("0.00020000"),
                timestamp=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
                quality_operation_type="recorded_evaluation",
                quality_operation_id=uuid4(),
                usage_purpose="recorded_semantic_judge",
            ),
        ]
    )
    await db_session.flush()

    response = await get_usage_breakdown(
        ExecutionContext(
            user=UserPrincipal(
                user_id=uuid4(),
                email="admin@example.com",
                organization_id=ctx_org.id,
                is_superuser=True,
            ),
            org_id=ctx_org.id,
            db=db_session,
        ),
        UserPrincipal(
            user_id=uuid4(),
            email="admin@example.com",
            organization_id=ctx_org.id,
            is_superuser=True,
        ),
        db_session,
        start_date=date(2026, 9, 20),
        end_date=date(2026, 9, 20),
        org_id=explicit_org.id,
        limit=50,
        offset=0,
    )

    assert response.overall.input_tokens == 7
    assert response.model_dump(mode="json")["overall"]["known_cost"] == "0.00020000"


async def test_public_usage_routes_reject_org_id_with_global_only(db_session):
    from src.routers.usage_reports import get_usage_breakdown, get_usage_report

    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
    )
    ctx = ExecutionContext(user=user, org_id=None, db=db_session)

    with pytest.raises(HTTPException) as report_exc:
        await get_usage_report(
            ctx,
            user,
            db_session,
            start_date=date(2026, 9, 20),
            end_date=date(2026, 9, 20),
            org_id=str(uuid4()),
            global_only=True,
        )
    assert report_exc.value.status_code == 422

    with pytest.raises(HTTPException) as breakdown_exc:
        await get_usage_breakdown(
            ctx,
            user,
            db_session,
            start_date=date(2026, 9, 20),
            end_date=date(2026, 9, 20),
            org_id=uuid4(),
            global_only=True,
        )
    assert breakdown_exc.value.status_code == 422


async def test_public_usage_report_global_only_excludes_tenant_rows(db_session):
    from src.routers.usage_reports import get_usage_report

    org = await _seed_org(db_session, name="Tenant Org")
    global_execution = Execution(
        workflow_name="Global Workflow",
        status=ExecutionStatus.SUCCESS,
        started_at=_dt(),
        executed_by_name="test",
        organization_id=None,
        cpu_total_seconds=1.5,
        peak_memory_bytes=2048,
    )
    tenant_execution = Execution(
        workflow_name="Tenant Workflow",
        status=ExecutionStatus.SUCCESS,
        started_at=_dt(),
        executed_by_name="test",
        organization_id=org.id,
        cpu_total_seconds=9.0,
        peak_memory_bytes=8192,
    )
    db_session.add_all([global_execution, tenant_execution])
    await db_session.flush()
    db_session.add_all(
        [
            _usage(
                execution_id=global_execution.id,
                organization_id=None,
                input_tokens=10,
                output_tokens=5,
                cost=Decimal("0.10000000"),
            ),
            _usage(
                execution_id=tenant_execution.id,
                organization_id=org.id,
                input_tokens=100,
                output_tokens=50,
                cost=Decimal("1.00000000"),
            ),
            KnowledgeStorageDaily(
                snapshot_date=date(2026, 9, 20),
                organization_id=None,
                namespace="global",
                document_count=3,
                size_bytes=1024,
            ),
            KnowledgeStorageDaily(
                snapshot_date=date(2026, 9, 20),
                organization_id=org.id,
                namespace="tenant",
                document_count=30,
                size_bytes=4096,
            ),
        ]
    )
    await db_session.flush()

    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
    )
    response = await get_usage_report(
        ExecutionContext(user=user, org_id=None, db=db_session),
        user,
        db_session,
        start_date=date(2026, 9, 20),
        end_date=date(2026, 9, 20),
        source="all",
        org_id=None,
        global_only=True,
    )

    assert response.summary.total_input_tokens == 10
    assert response.summary.total_ai_cost == Decimal("0.10000000")
    assert response.summary.total_cpu_seconds == 1.5
    assert response.summary.peak_memory_bytes == 2048
    assert [row.workflow_name for row in response.by_workflow] == ["Global Workflow"]
    assert response.by_organization == []
    assert [row.namespace for row in response.knowledge_storage] == ["global"]
    assert response.knowledge_storage_trends[0].total_documents == 3


async def test_public_usage_breakdown_route_rejects_unbounded_end_date(db_session):
    from src.routers.usage_reports import get_usage_breakdown

    with pytest.raises(HTTPException) as exc:
        await get_usage_breakdown(
            ExecutionContext(
                user=UserPrincipal(
                    user_id=uuid4(),
                    email="admin@example.com",
                    organization_id=None,
                    is_superuser=True,
                ),
                org_id=None,
                db=db_session,
            ),
            UserPrincipal(
                user_id=uuid4(),
                email="admin@example.com",
                organization_id=None,
                is_superuser=True,
            ),
            db_session,
            start_date=date.max,
            end_date=date.max,
            limit=50,
            offset=0,
        )

    assert exc.value.status_code == 422


async def test_read_snapshot_db_keeps_multi_query_report_coherent(
    async_session_factory, monkeypatch
):
    initial_operation_id = uuid4()
    later_operation_id = uuid4()
    org_id = uuid4()

    async with async_session_factory() as seed_session:
        seed_session.add(
            Organization(
                id=org_id, name=f"Snapshot Org {org_id}", created_by="test@example.com"
            )
        )
        seed_session.add(
            _usage(
                organization_id=org_id,
                input_tokens=10,
                output_tokens=1,
                quality_operation_type="recorded_evaluation",
                quality_operation_id=initial_operation_id,
                usage_purpose="recorded_semantic_judge",
            )
        )
        await seed_session.commit()

    original_overall = reporting._overall

    async def overall_then_commit_later_usage(session, filters):
        result = await original_overall(session, filters)
        async with async_session_factory() as second_session:
            second_session.add(
                _usage(
                    organization_id=org_id,
                    input_tokens=99,
                    output_tokens=9,
                    quality_operation_type="recorded_evaluation",
                    quality_operation_id=later_operation_id,
                    usage_purpose="recorded_semantic_judge",
                )
            )
            await second_session.commit()
        return result

    monkeypatch.setattr(reporting, "_overall", overall_then_commit_later_usage)
    monkeypatch.setattr(db_deps, "get_session_factory", lambda: async_session_factory)

    try:
        dependency = db_deps.get_read_snapshot_db()
        snapshot_session = await dependency.__anext__()
        try:
            report = await reporting.summarize_quality_usage(
                snapshot_session,
                QualityUsageReportFilters(scope="organization", organization_id=org_id),
            )
            with pytest.raises(StopAsyncIteration):
                await dependency.__anext__()
        except Exception:
            await dependency.aclose()
            raise

        assert report["overall"]["input_tokens"] == 10
        assert report["by_purpose"]["items"][0]["totals"]["input_tokens"] == 10
        assert report["by_operation"]["total_groups"] == 1

        monkeypatch.setattr(reporting, "_overall", original_overall)
        async with async_session_factory() as next_session:
            next_report = await reporting.summarize_quality_usage(
                next_session,
                QualityUsageReportFilters(scope="organization", organization_id=org_id),
            )
            assert next_report["overall"]["input_tokens"] == 109
            assert next_report["by_operation"]["total_groups"] == 2
    finally:
        async with async_session_factory() as cleanup_session:
            await cleanup_session.execute(
                delete(AIUsage).where(
                    AIUsage.quality_operation_id.in_(
                        [initial_operation_id, later_operation_id]
                    )
                )
            )
            await cleanup_session.execute(
                delete(Organization).where(Organization.id == org_id)
            )
            await cleanup_session.commit()
