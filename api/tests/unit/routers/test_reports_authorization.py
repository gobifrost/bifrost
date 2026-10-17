"""Real-DB original-caller coverage for usage and resource reports."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.ai_usage import AIUsage
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.routers import usage_reports
from src.services.authorization.enforce import load_caller


async def _reader(
    db: AsyncSession,
    *,
    metrics_boundaries: tuple[str, ...],
    execution_home_only: bool = False,
) -> tuple[UserPrincipal, Organization, Organization, Organization]:
    home, fabrikam, foreign = (
        Organization(name=f"{name} {uuid4().hex[:8]}", created_by="test")
        for name in ("Contoso", "Fabrikam", "Initech")
    )
    db.add_all([home, fabrikam, foreign])
    await db.flush()

    person = User(
        email=f"metrics-reader-{uuid4().hex}@contoso.example",
        organization_id=home.id,
        base_role_id=USER_ROLE_ID,
    )
    metrics_role = Role(name=f"Metrics reader {uuid4().hex}", created_by="test")
    execution_role = Role(name=f"Execution reader {uuid4().hex}", created_by="test")
    db.add_all([person, metrics_role, execution_role])
    await db.flush()

    db.add_all([
        UserRole(user_id=person.id, role_id=metrics_role.id, assigned_by="test"),
        UserRole(user_id=person.id, role_id=execution_role.id, assigned_by="test"),
        RolePermission(role_id=metrics_role.id, permission="metrics.read"),
        RolePermission(role_id=execution_role.id, permission="executions.read.all"),
    ])
    await db.flush()

    organizations = {"home": home, "fabrikam": fabrikam}
    for boundary in metrics_boundaries:
        if boundary == "global":
            db.add(UserRoleBoundary(user_id=person.id, role_id=metrics_role.id, kind="platform"))
        else:
            db.add(
                UserRoleBoundary(
                    user_id=person.id,
                    role_id=metrics_role.id,
                    kind="organization",
                    organization_id=organizations[boundary].id,
                )
            )
    if execution_home_only:
        db.add(
            UserRoleBoundary(
                user_id=person.id,
                role_id=execution_role.id,
                kind="organization",
                organization_id=home.id,
            )
        )
    await db.flush()

    return (
        UserPrincipal(user_id=person.id, email=person.email, organization_id=home.id),
        home,
        fabrikam,
        foreign,
    )


async def _execution_and_usage(
    db: AsyncSession,
    *,
    organization_id,
    workflow_name: str,
    input_tokens: int,
    timestamp: datetime,
) -> Execution:
    execution = Execution(
        workflow_name=workflow_name,
        status=ExecutionStatus.SUCCESS,
        parameters={},
        executed_by_name="Synthetic runner",
        organization_id=organization_id,
        started_at=timestamp,
        completed_at=timestamp + timedelta(seconds=2),
        duration_ms=2_000,
        cpu_total_seconds=2.0,
    )
    db.add(execution)
    await db.flush()
    db.add(
        AIUsage(
            execution_id=execution.id,
            provider="synthetic",
            model="contoso-model",
            input_tokens=input_tokens,
            output_tokens=input_tokens // 2,
            cost=Decimal("0.10"),
            timestamp=timestamp,
            organization_id=organization_id,
        )
    )
    await db.flush()
    return execution


async def test_usage_report_scopes_summary_and_breakdowns_to_named_metrics_placements(
    db_session: AsyncSession,
):
    principal, home, fabrikam, foreign = await _reader(
        db_session, metrics_boundaries=("home", "fabrikam")
    )
    now = datetime.now(UTC)
    await _execution_and_usage(
        db_session, organization_id=home.id, workflow_name="Contoso workflow", input_tokens=10, timestamp=now
    )
    await _execution_and_usage(
        db_session, organization_id=fabrikam.id, workflow_name="Fabrikam workflow", input_tokens=20, timestamp=now
    )
    await _execution_and_usage(
        db_session, organization_id=foreign.id, workflow_name="Foreign workflow", input_tokens=40, timestamp=now
    )

    report = await usage_reports.get_usage_report(
        SimpleNamespace(),
        principal,
        db_session,
        start_date=now.date(),
        end_date=now.date(),
        source="executions",
        org_id=None,
    )

    assert report.summary.total_input_tokens == 30
    assert report.summary.total_ai_calls == 2
    assert [trend.input_tokens for trend in report.trends] == [30]
    assert sum(row.input_tokens for row in report.by_workflow) == 30
    assert {row.organization_id for row in report.by_organization} == {str(home.id), str(fabrikam.id)}
    assert sum(row.input_tokens for row in report.by_organization) == 30


async def test_usage_report_refuses_foreign_scope_before_aggregate_queries(
    db_session: AsyncSession,
    monkeypatch,
):
    principal, _home, _fabrikam, foreign = await _reader(
        db_session, metrics_boundaries=("home",)
    )
    caller = await load_caller(db_session, principal)
    monkeypatch.setattr(usage_reports, "load_caller", AsyncMock(return_value=caller))
    aggregate_query = AsyncMock(side_effect=AssertionError("aggregate query ran before foreign-scope refusal"))
    monkeypatch.setattr(db_session, "execute", aggregate_query)

    today = date.today()
    with pytest.raises(HTTPException) as error:
        await usage_reports.get_usage_report(
            SimpleNamespace(),
            principal,
            db_session,
            start_date=today,
            end_date=today,
            source="all",
            org_id=str(foreign.id),
        )

    assert error.value.status_code == 403
    aggregate_query.assert_not_awaited()


async def test_usage_report_global_scope_includes_only_global_usage(db_session: AsyncSession):
    principal, home, _fabrikam, _foreign = await _reader(
        db_session, metrics_boundaries=("global",)
    )
    now = datetime.now(UTC)
    await _execution_and_usage(
        db_session, organization_id=None, workflow_name="Global workflow", input_tokens=7, timestamp=now
    )
    await _execution_and_usage(
        db_session, organization_id=home.id, workflow_name="Contoso workflow", input_tokens=13, timestamp=now
    )

    report = await usage_reports.get_usage_report(
        SimpleNamespace(),
        principal,
        db_session,
        start_date=now.date(),
        end_date=now.date(),
        source="executions",
        org_id="global",
    )

    assert report.summary.total_input_tokens == 7
    assert [trend.input_tokens for trend in report.trends] == [7]
    assert sum(row.input_tokens for row in report.by_workflow) == 7
    assert report.by_organization == []


async def test_workflow_resource_reports_intersect_metrics_reach_and_execution_history_visibility(
    db_session: AsyncSession,
):
    principal, home, fabrikam, foreign = await _reader(
        db_session,
        metrics_boundaries=("home", "fabrikam"),
        execution_home_only=True,
    )
    now = datetime.now(UTC)
    home_execution = await _execution_and_usage(
        db_session, organization_id=home.id, workflow_name="Contoso workflow", input_tokens=10, timestamp=now
    )
    await _execution_and_usage(
        db_session, organization_id=fabrikam.id, workflow_name="Fabrikam workflow", input_tokens=20, timestamp=now
    )
    await _execution_and_usage(
        db_session, organization_id=foreign.id, workflow_name="Foreign workflow", input_tokens=40, timestamp=now
    )
    window_start = now - timedelta(minutes=1)
    window_end = now + timedelta(minutes=1)

    runs = await usage_reports.get_workflow_resource_report(
        db_session,
        principal,
        started_after=window_start,
        started_before=window_end,
        view="runs",
        sort="started",
        page=1,
        page_size=25,
        org_id=None,
        workflow_id=None,
        workflow=None,
        status=None,
    )
    workflows = await usage_reports.get_workflow_resource_report(
        db_session,
        principal,
        started_after=window_start,
        started_before=window_end,
        view="workflows",
        sort="started",
        page=1,
        page_size=25,
        org_id=None,
        workflow_id=None,
        workflow=None,
        status=None,
    )

    assert runs.summary.run_count == 1
    assert runs.total == 1
    assert [row.execution_id for row in runs.runs] == [str(home_execution.id)]
    assert workflows.summary.run_count == 2
    assert workflows.total == 2
    assert {row.workflow_name for row in workflows.workflows} == {"Contoso workflow", "Fabrikam workflow"}
