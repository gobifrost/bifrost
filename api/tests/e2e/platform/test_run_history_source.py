"""
One run history source: kept executions plus the daily rollup.

Every finished run is counted once, in ``executions`` while kept and in
``workflow_run_daily`` after retention deletes it, so every reader reports the
same totals before and after the deletion loop. The seeded runs finished in
2001 and the cutoff is 2002, so only this test's rows are eligible.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm import AIUsage, Execution, Organization, Workflow, WorkflowRunDaily
from src.services.run_retention.deleter import delete_workflow_run_batch
from src.services.run_retention.source import all_time_run_totals, workflow_run_source

CUTOFF = datetime(2002, 1, 1, tzinfo=UTC)
DAY = datetime(2001, 1, 1, tzinfo=UTC)


@dataclass
class Seeded:
    org_id: UUID
    workflow_id: UUID
    wf_name: str
    execution_ids: list[UUID]
    total_ms: int
    total_cpu: float
    max_cores: float
    max_rss: int
    ai_cost: Decimal


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    hex_ = uuid4().hex[:12]
    org = Organization(id=uuid4(), name=f"History Org {hex_}", created_by="test")
    wf_name = f"rr-src-{hex_}"
    workflow = Workflow(
        id=uuid4(),
        name=wf_name,
        function_name="rr_src",
        path=f"workflows/rr_src_{hex_}.py",
        type="workflow",
        access_level="authenticated",
        is_active=True,
    )
    db_session.add_all([org, workflow])
    await db_session.flush()

    runs = [
        (ExecutionStatus.SUCCESS, 10, 4000, 1.5, 0.5, 100, 90),
        (ExecutionStatus.SUCCESS, 11, 2000, 0.25, 0.75, 300, 50),
        (ExecutionStatus.FAILED, 12, 1000, 0.5, 1.25, 200, 70),
    ]
    executions = [
        Execution(
            id=uuid4(),
            workflow_name=wf_name,
            workflow_id=workflow.id,
            organization_id=org.id,
            status=status,
            executed_by_name="History Test",
            started_at=DAY.replace(hour=hour),
            completed_at=DAY.replace(hour=hour, second=5),
            duration_ms=ms,
            cpu_total_seconds=cpu,
            peak_cpu_cores=cores,
            peak_process_rss_bytes=rss,
            peak_memory_bytes=mem,
        )
        for status, hour, ms, cpu, cores, rss, mem in runs
    ]
    db_session.add_all(executions)
    await db_session.flush()
    db_session.add_all([
        AIUsage(execution_id=executions[0].id, organization_id=org.id, provider="openai", model="m",
                input_tokens=10, output_tokens=5, cost=Decimal("0.01"), timestamp=DAY.replace(hour=10)),
        AIUsage(execution_id=executions[0].id, organization_id=org.id, provider="openai", model="m",
                input_tokens=20, output_tokens=5, cost=Decimal("0.02"), timestamp=DAY.replace(hour=10)),
    ])
    await db_session.commit()

    data = Seeded(
        org_id=org.id,
        workflow_id=workflow.id,
        wf_name=wf_name,
        execution_ids=[e.id for e in executions],
        total_ms=7000,
        total_cpu=2.25,
        max_cores=1.25,
        max_rss=300,
        ai_cost=Decimal("0.03"),
    )
    db_session.expunge_all()

    yield data

    await db_session.rollback()
    await db_session.execute(delete(AIUsage).where(AIUsage.execution_id.in_(data.execution_ids)))
    await db_session.execute(delete(Execution).where(Execution.id.in_(data.execution_ids)))
    await db_session.execute(delete(WorkflowRunDaily).where(WorkflowRunDaily.workflow_name == data.wf_name))
    await db_session.execute(delete(Workflow).where(Workflow.id == data.workflow_id))
    await db_session.execute(delete(Organization).where(Organization.id == data.org_id))
    await db_session.commit()


async def _delete_all_expired(lease) -> None:
    while await delete_workflow_run_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token, limit=2):
        pass


async def _assert_rolled_up(db: AsyncSession, seeded: Seeded) -> None:
    kept = await db.scalar(select(func.count()).select_from(Execution).where(Execution.id.in_(seeded.execution_ids)))
    rolled = await db.scalar(
        select(func.sum(WorkflowRunDaily.run_count)).where(WorkflowRunDaily.workflow_name == seeded.wf_name)
    )
    await db.commit()
    assert (kept, rolled) == (0, 3)


async def test_totals_identical_before_and_after_deletion(db_session, seeded, run_retention_lease):
    window = dict(start=DAY, end=datetime(2001, 1, 2, tzinfo=UTC), workflow_name_like=seeded.wf_name)

    async def totals():
        src = workflow_run_source(**window)
        row = (await db_session.execute(select(
            func.sum(src.c.run_count), func.sum(src.c.total_duration_ms), func.sum(src.c.total_cpu_seconds),
            func.max(src.c.max_peak_cpu_cores), func.max(src.c.max_peak_process_rss_bytes)))).one()
        await db_session.commit()
        return tuple(row)

    before = await totals()
    await _delete_all_expired(run_retention_lease)
    await _assert_rolled_up(db_session, seeded)
    after = await totals()
    assert before == after == (3, seeded.total_ms, seeded.total_cpu, seeded.max_cores, seeded.max_rss)


async def test_usage_report_identical_before_and_after_deletion(
    db_session, seeded, run_retention_lease, e2e_client, platform_admin
):
    def report():
        response = e2e_client.get(
            "/api/reports/usage",
            headers=platform_admin.headers,
            params={
                "start_date": "2001-01-01",
                "end_date": "2001-01-01",
                "source": "executions",
                "org_id": str(seeded.org_id),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        entry = next(w for w in body["by_workflow"] if w["workflow_name"] == seeded.wf_name)
        return (
            body["summary"]["total_ai_cost"],
            body["summary"]["total_cpu_seconds"],
            (entry["workflow_name"], entry["ai_cost"], entry["execution_count"], entry["cpu_seconds"]),
        )

    before = report()
    await _delete_all_expired(run_retention_lease)
    await _assert_rolled_up(db_session, seeded)
    after = report()
    assert before == after
    _, _, (_, ai_cost, execution_count, cpu_seconds) = after
    assert (Decimal(ai_cost), execution_count, cpu_seconds) == (seeded.ai_cost, 1, seeded.total_cpu)


async def test_workflow_resource_report_identical_before_and_after_deletion(
    db_session, seeded, run_retention_lease, e2e_client, platform_admin
):
    def report():
        response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params={
                "view": "workflows",
                "started_after": "2001-01-01T00:00:00Z",
                "started_before": "2001-01-02T00:00:00Z",
                "workflow": seeded.wf_name,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        (row,) = body["workflows"]
        return (
            body["summary"]["run_count"],
            body["summary"]["total_cpu_seconds"],
            body["summary"]["total_ai_cost"],
            row["workflow_id"],
            row["failed_count"],
            row["total_ai_cost"],
        )

    before = report()
    await _delete_all_expired(run_retention_lease)
    await _assert_rolled_up(db_session, seeded)
    after = report()
    assert before == after
    run_count, cpu, summary_ai, workflow_id, failed, ai = after
    assert (run_count, cpu, workflow_id, failed) == (3, seeded.total_cpu, str(seeded.workflow_id), 1)
    assert Decimal(summary_ai) == Decimal(ai) == seeded.ai_cost


async def test_workflows_view_accepts_a_year_and_runs_view_a_month(e2e_client, platform_admin):
    year = {"started_after": "2001-01-01T00:00:00Z", "started_before": "2002-01-01T00:00:00Z"}
    workflows = e2e_client.get(
        "/api/reports/workflow-resources", headers=platform_admin.headers, params={**year, "view": "workflows"}
    )
    assert workflows.status_code == 200, workflows.text
    runs = e2e_client.get(
        "/api/reports/workflow-resources", headers=platform_admin.headers, params={**year, "view": "runs"}
    )
    assert runs.status_code == 422
    assert "runs view" in runs.json()["detail"]


async def test_all_time_totals_identical_before_and_after_deletion(db_session, seeded, run_retention_lease):
    before = await all_time_run_totals(db_session)
    await db_session.commit()
    await _delete_all_expired(run_retention_lease)
    await _assert_rolled_up(db_session, seeded)
    after = await all_time_run_totals(db_session)
    await db_session.commit()
    assert set(before) == {"total", "success", "failed"}
    assert before == after


async def test_workflow_metrics_count_kept_and_rolled_up_runs(db_session, e2e_client, platform_admin):
    name = f"rr-metrics-{uuid4().hex[:12]}"
    now = datetime.now(UTC)
    kept = Execution(
        id=uuid4(), workflow_name=name, status=ExecutionStatus.SUCCESS, executed_by_name="History Test",
        started_at=now - timedelta(minutes=5), completed_at=now - timedelta(minutes=4),
        duration_ms=3000, cpu_total_seconds=0.5, peak_memory_bytes=100,
    )
    rolled = WorkflowRunDaily(
        day=(now - timedelta(days=2)).date(), workflow_name=name, status=ExecutionStatus.FAILED,
        run_count=2, total_duration_ms=1000, total_cpu_seconds=1.0, max_peak_memory_bytes=300,
    )
    db_session.add_all([kept, rolled])
    await db_session.commit()
    try:
        response = e2e_client.get(
            "/api/metrics/workflows", headers=platform_admin.headers, params={"days": 7, "limit": 100}
        )
        assert response.status_code == 200, response.text
        (row,) = [w for w in response.json()["workflows"] if w["workflow_name"] == name]
        assert (row["total_executions"], row["success_count"], row["failed_count"]) == (3, 1, 2)
        assert (row["avg_duration_ms"], row["avg_cpu_seconds"]) == (1333, 0.5)
        assert (row["peak_memory_bytes"], row["avg_memory_bytes"]) == (300, 200)
    finally:
        await db_session.execute(delete(Execution).where(Execution.id == kept.id))
        await db_session.execute(delete(WorkflowRunDaily).where(WorkflowRunDaily.workflow_name == name))
        await db_session.commit()
