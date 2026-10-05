"""
One run history source: kept executions plus the daily rollup.

Every finished run is counted once, in ``executions`` while kept and in
``workflow_run_daily`` after retention deletes it, so every reader reports the
same totals before and after the deletion loop. The seeded runs finished in
2001 and the cutoff is 2002, so only this test's rows are eligible. One run is
renamed, one starts on 2001-01-02, just outside the windows the tests read, and
one was cancelled before it started, so it has only a completion time.
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
NEXT_DAY = datetime(2001, 1, 2, tzinfo=UTC)


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

    jan_1 = (DAY.replace(hour=hour) for hour in range(10, 14))
    runs = [
        (wf_name, ExecutionStatus.SUCCESS, next(jan_1), 4000, 1.5, 0.5, 100, 90),
        (wf_name, ExecutionStatus.SUCCESS, next(jan_1), 2000, 0.25, 0.75, 300, 50),
        (wf_name, ExecutionStatus.FAILED, next(jan_1), 1000, 0.5, 1.25, 200, 70),
        (f"{wf_name}-renamed", ExecutionStatus.SUCCESS, next(jan_1), 500, 0.125, 0.25, 50, 10),
        (wf_name, ExecutionStatus.SUCCESS, NEXT_DAY.replace(hour=15), 9000, 4.0, 2.0, 999, 999),
    ]
    executions = [
        Execution(
            id=uuid4(),
            workflow_name=name,
            workflow_id=workflow.id,
            organization_id=org.id,
            status=status,
            executed_by_name="History Test",
            started_at=started,
            completed_at=started + timedelta(seconds=5),
            duration_ms=ms,
            cpu_total_seconds=cpu,
            peak_cpu_cores=cores,
            peak_process_rss_bytes=rss,
            peak_memory_bytes=mem,
        )
        for name, status, started, ms, cpu, cores, rss, mem in runs
    ]
    executions.append(
        Execution(
            id=uuid4(),
            workflow_name=wf_name,
            workflow_id=workflow.id,
            organization_id=org.id,
            status=ExecutionStatus.CANCELLED,
            executed_by_name="History Test",
            completed_at=DAY.replace(hour=14),
        )
    )
    db_session.add_all(executions)
    await db_session.flush()
    usage = [(0, "0.01"), (0, "0.02"), (2, "0.04"), (3, "0.08")]
    db_session.add_all([
        AIUsage(execution_id=executions[i].id, organization_id=org.id, provider="openai", model="m",
                input_tokens=10, output_tokens=5, cost=Decimal(cost), timestamp=DAY.replace(hour=10))
        for i, cost in usage
    ])
    await db_session.commit()

    data = Seeded(
        org_id=org.id,
        workflow_id=workflow.id,
        wf_name=wf_name,
        execution_ids=[e.id for e in executions],
        total_ms=7500,
        total_cpu=2.375,
        max_cores=1.25,
        max_rss=300,
        ai_cost=Decimal("0.15"),
    )
    db_session.expunge_all()

    yield data

    await db_session.rollback()
    await db_session.execute(delete(AIUsage).where(AIUsage.execution_id.in_(data.execution_ids)))
    await db_session.execute(delete(Execution).where(Execution.id.in_(data.execution_ids)))
    await db_session.execute(delete(WorkflowRunDaily).where(WorkflowRunDaily.workflow_id == data.workflow_id))
    await db_session.execute(delete(Workflow).where(Workflow.id == data.workflow_id))
    await db_session.execute(delete(Organization).where(Organization.id == data.org_id))
    await db_session.commit()


async def _delete_all_expired(lease) -> None:
    while await delete_workflow_run_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token, limit=2):
        pass


async def _assert_rolled_up(db: AsyncSession, seeded: Seeded) -> None:
    kept = await db.scalar(select(func.count()).select_from(Execution).where(Execution.id.in_(seeded.execution_ids)))
    rolled = await db.scalar(
        select(func.sum(WorkflowRunDaily.run_count)).where(WorkflowRunDaily.workflow_id == seeded.workflow_id)
    )
    await db.commit()
    assert (kept, rolled) == (0, 6)


async def test_totals_identical_before_and_after_deletion(db_session, seeded, run_retention_lease):
    # The window ends at 2001-01-02 midnight: the run starting that day is out,
    # kept or rolled up.
    window = dict(start=DAY, end=NEXT_DAY, workflow_name_like=seeded.wf_name)

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
    assert before == after == (5, seeded.total_ms, seeded.total_cpu, seeded.max_cores, seeded.max_rss)


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
    assert (Decimal(ai_cost), execution_count, cpu_seconds) == (seeded.ai_cost, 3, seeded.total_cpu)


async def test_workflow_resource_report_identical_before_and_after_deletion(
    db_session, seeded, run_retention_lease, e2e_client, platform_admin
):
    def report(**extra):
        response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params={
                "view": "workflows",
                "started_after": "2001-01-01T00:00:00Z",
                "started_before": "2001-01-02T00:00:00Z",
                "workflow": seeded.wf_name,
                **extra,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # AI cost rolls up with its runs, so the rows always add up to the summary.
        assert sum(Decimal(row["total_ai_cost"]) for row in body["workflows"]) == Decimal(
            body["summary"]["total_ai_cost"]
        )
        rows = {
            row["workflow_name"]: (row["workflow_id"], row["run_count"], row["failed_count"],
                                   Decimal(row["total_ai_cost"]))
            for row in body["workflows"]
        }
        summary = body["summary"]
        return summary["run_count"], summary["total_cpu_seconds"], Decimal(summary["total_ai_cost"]), rows

    def both():
        return report(), report(status="Failed")

    before = both()
    await _delete_all_expired(run_retention_lease)
    await _assert_rolled_up(db_session, seeded)
    after = both()
    assert before == after
    workflow_id = str(seeded.workflow_id)
    assert after[0] == (5, seeded.total_cpu, seeded.ai_cost, {
        seeded.wf_name: (workflow_id, 4, 1, Decimal("0.07")),
        f"{seeded.wf_name}-renamed": (workflow_id, 1, 0, Decimal("0.08")),
    })
    assert after[1] == (1, 0.5, Decimal("0.04"), {seeded.wf_name: (workflow_id, 1, 1, Decimal("0.04"))})


async def test_usage_report_names_kept_inline_runs_and_merges_deleted_ones(
    db_session, run_retention_lease, e2e_client, platform_admin
):
    org_id = uuid4()
    org = Organization(id=org_id, name=f"Inline Org {uuid4().hex[:12]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    names = [f"rr-inline-{uuid4().hex[:12]}" for _ in range(2)]
    execution_ids = [uuid4() for _ in names]
    executions = [
        Execution(
            id=execution_id, workflow_name=name, organization_id=org_id, status=ExecutionStatus.SUCCESS,
            executed_by_name="History Test", started_at=DAY.replace(hour=10), completed_at=DAY.replace(hour=11),
        )
        for execution_id, name in zip(execution_ids, names, strict=True)
    ]
    db_session.add_all(executions)
    await db_session.flush()
    db_session.add_all([
        AIUsage(execution_id=execution_id, organization_id=org_id, provider="openai", model="m",
                input_tokens=10, output_tokens=5, cost=Decimal(cost), timestamp=DAY.replace(hour=10))
        for execution_id, cost in zip(execution_ids, ("0.01", "0.02"), strict=True)
    ])
    await db_session.commit()

    def rows():
        response = e2e_client.get(
            "/api/reports/usage",
            headers=platform_admin.headers,
            params={"start_date": "2001-01-01", "end_date": "2001-01-01", "source": "executions",
                    "org_id": str(org_id)},
        )
        assert response.status_code == 200, response.text
        return {
            row["workflow_name"]: (row["execution_count"], Decimal(row["ai_cost"]))
            for row in response.json()["by_workflow"]
        }

    try:
        assert rows() == {names[0]: (1, Decimal("0.01")), names[1]: (1, Decimal("0.02"))}
        await _delete_all_expired(run_retention_lease)
        # Deleted inline runs leave no name on their usage, so they share one row.
        assert rows() == {"Inline or deleted workflow": (2, Decimal("0.03"))}
    finally:
        await db_session.rollback()
        await db_session.execute(delete(AIUsage).where(AIUsage.execution_id.in_(execution_ids)))
        await db_session.execute(delete(Execution).where(Execution.id.in_(execution_ids)))
        await db_session.execute(delete(WorkflowRunDaily).where(WorkflowRunDaily.organization_id == org_id))
        await db_session.execute(delete(Organization).where(Organization.id == org_id))
        await db_session.commit()


async def test_workflows_view_window_cap_is_366_days_and_runs_view_31(e2e_client, platform_admin):
    def get(view: str, started_before: str):
        return e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params={"view": view, "started_after": "2001-01-01T00:00:00Z", "started_before": started_before},
        )

    assert get("workflows", "2002-01-02T00:00:00Z").status_code == 200
    too_long = get("workflows", "2002-01-03T00:00:00Z")
    assert too_long.status_code == 422
    assert "366 days for the workflows view" in too_long.json()["detail"]
    assert get("runs", "2001-02-01T00:00:00Z").status_code == 200
    runs = get("runs", "2001-02-02T00:00:00Z")
    assert runs.status_code == 422
    assert "31 days for the runs view" in runs.json()["detail"]


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
    in_flight = Execution(
        id=uuid4(), workflow_name=name, status=ExecutionStatus.RUNNING, executed_by_name="History Test",
        started_at=now - timedelta(minutes=1),
    )
    rolled = WorkflowRunDaily(
        day=(now - timedelta(days=2)).date(), workflow_name=name, status=ExecutionStatus.FAILED,
        run_count=2, total_duration_ms=1000, total_cpu_seconds=1.0, max_peak_memory_bytes=300,
    )
    db_session.add_all([kept, in_flight, rolled])
    await db_session.commit()
    try:
        response = e2e_client.get(
            "/api/metrics/workflows", headers=platform_admin.headers, params={"days": 7, "limit": 100}
        )
        assert response.status_code == 200, response.text
        (row,) = [w for w in response.json()["workflows"] if w["workflow_name"] == name]
        # The in-flight run counts but has no duration or CPU, so the averages skip it.
        assert (row["total_executions"], row["success_count"], row["failed_count"]) == (4, 1, 2)
        assert (row["avg_duration_ms"], row["avg_cpu_seconds"]) == (1333, 0.5)
        assert (row["peak_memory_bytes"], row["avg_memory_bytes"]) == (300, 200)
    finally:
        await db_session.execute(delete(Execution).where(Execution.id.in_([kept.id, in_flight.id])))
        await db_session.execute(delete(WorkflowRunDaily).where(WorkflowRunDaily.workflow_name == name))
        await db_session.commit()
