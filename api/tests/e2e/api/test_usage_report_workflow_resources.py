"""Usage Reports must count resource use for workflows without AI calls."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.ai_usage import AIUsage
from src.models.orm.executions import Execution


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workflow_resources_include_non_ai_runs_without_multiplying_cpu(
    db_session, e2e_client, platform_admin, org1,
):
    organization_id = UUID(org1["id"])
    workflow_name = f"resource-report-{uuid4()}"
    started_at = datetime.now(timezone.utc)
    plain_run = Execution(
        workflow_name=workflow_name, executed_by_name="Test", organization_id=organization_id,
        started_at=started_at, cpu_total_seconds=120, peak_memory_bytes=200_000_000,
    )
    ai_run = Execution(
        workflow_name=workflow_name, executed_by_name="Test", organization_id=organization_id,
        started_at=started_at, cpu_total_seconds=30, peak_memory_bytes=100_000_000,
    )
    db_session.add_all([plain_run, ai_run])
    await db_session.flush()
    db_session.add_all([
        AIUsage(execution_id=ai_run.id, organization_id=organization_id, provider="test",
                model="test", input_tokens=10, output_tokens=5, cost=Decimal("0.01"),
                timestamp=started_at),
        AIUsage(execution_id=ai_run.id, organization_id=organization_id, provider="test",
                model="test", input_tokens=20, output_tokens=10, cost=Decimal("0.02"),
                timestamp=started_at),
    ])
    await db_session.commit()

    try:
        response = e2e_client.get(
            "/api/reports/usage", headers=platform_admin.headers,
            params={"start_date": date.today().isoformat(), "end_date": date.today().isoformat(),
                    "source": "executions", "org_id": str(organization_id)},
        )
        assert response.status_code == 200, response.text
        workflow = next(row for row in response.json()["by_workflow"]
                        if row["workflow_name"] == workflow_name)
        assert workflow["execution_count"] == 2
        assert workflow["cpu_seconds"] == 150
        assert workflow["memory_bytes"] == 200_000_000
        assert workflow["input_tokens"] == 30
        assert Decimal(workflow["ai_cost"]) == Decimal("0.03")
    finally:
        await db_session.execute(delete(AIUsage).where(AIUsage.execution_id == ai_run.id))
        await db_session.execute(delete(Execution).where(Execution.id.in_([plain_run.id, ai_run.id])))
        await db_session.commit()
