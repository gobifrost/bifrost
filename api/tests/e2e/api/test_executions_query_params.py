"""Request-level regressions for /api/executions query handling."""

from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.workflows import Workflow


pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def seeded_execution_history(
    db_session: AsyncSession,
    platform_admin,
) -> AsyncGenerator[dict, None]:
    assert platform_admin.user_id is not None
    workflow_id = uuid4()
    base = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    rows: list[Execution] = []

    workflow = Workflow(
        id=workflow_id,
        name=f"query_param_history_{workflow_id.hex[:8]}",
        function_name="query_param_history",
        description="Query param history test workflow",
        path=f"workflows/query_param_history_{workflow_id.hex[:8]}.py",
        access_level="authenticated",
        is_active=True,
        created_at=base,
        updated_at=base,
    )
    db_session.add(workflow)

    for index in range(3):
        row = Execution(
            id=uuid4(),
            workflow_name=f"query-param-history-{workflow_id.hex[:8]}",
            workflow_id=workflow_id,
            status=ExecutionStatus.SUCCESS,
            parameters={},
            executed_by=platform_admin.user_id,
            executed_by_name=platform_admin.name,
            created_at=base + timedelta(minutes=index),
            started_at=base + timedelta(minutes=index),
            completed_at=base + timedelta(minutes=index, seconds=2),
        )
        db_session.add(row)
        rows.append(row)

    await db_session.commit()

    try:
        yield {"workflow_id": workflow_id, "base": base, "rows": rows}
    finally:
        for row in rows:
            await db_session.execute(delete(Execution).where(Execution.id == row.id))
        await db_session.execute(delete(Workflow).where(Workflow.id == workflow_id))
        await db_session.commit()


async def test_executions_accept_snake_case_aliases_and_camel_case_paging(
    e2e_client,
    platform_admin,
    seeded_execution_history,
):
    workflow_id = seeded_execution_history["workflow_id"]
    start_date = (
        seeded_execution_history["base"] - timedelta(minutes=1)
    ).isoformat().replace("+00:00", "Z")

    first = e2e_client.get(
        "/api/executions",
        params={
            "workflow_id": str(workflow_id),
            "workflow_name": "ignored-because-id-wins",
            "start_date": start_date,
            "exclude_local": "true",
            "limit": 1,
        },
        headers=platform_admin.headers,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    first_ids = [item["execution_id"] for item in first_body["executions"]]
    assert first_ids == [str(seeded_execution_history["rows"][2].id)]
    assert first_body["continuation_token"] is not None

    second = e2e_client.get(
        "/api/executions",
        params={
            "workflow_id": str(workflow_id),
            "start_date": start_date,
            "limit": 1,
            "continuationToken": first_body["continuation_token"],
        },
        headers=platform_admin.headers,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    second_ids = [item["execution_id"] for item in second_body["executions"]]
    assert second_ids == [str(seeded_execution_history["rows"][1].id)]
    assert second_body["continuation_token"] is not None

    third = e2e_client.get(
        "/api/executions",
        params={
            "workflow_id": str(workflow_id),
            "start_date": start_date,
            "limit": 1,
            "continuation_token": second_body["continuation_token"],
        },
        headers=platform_admin.headers,
    )
    assert third.status_code == 200, third.text
    third_body = third.json()
    third_ids = [item["execution_id"] for item in third_body["executions"]]
    assert third_ids == [str(seeded_execution_history["rows"][0].id)]
    assert third_body["continuation_token"] is None


async def test_executions_rejects_unknown_query_params(
    e2e_client,
    platform_admin,
    seeded_execution_history,
):
    response = e2e_client.get(
        "/api/executions",
        params={
            "workflow_id": str(seeded_execution_history["workflow_id"]),
            "start_date": seeded_execution_history["base"].isoformat(),
            "bogus": "1",
        },
        headers=platform_admin.headers,
    )

    assert response.status_code == 422, response.text
    assert "bogus" in response.text


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("workflow_id", "not-a-uuid"),
        ("start_date", "not-a-date"),
        ("continuation_token", "not-a-cursor"),
        ("continuation_token", "-1"),
        ("exclude_local", "sometimes"),
    ],
)
async def test_executions_rejects_invalid_filter_values(
    e2e_client,
    platform_admin,
    seeded_execution_history,
    parameter,
    value,
):
    response = e2e_client.get(
        "/api/executions",
        params={parameter: value},
        headers=platform_admin.headers,
    )

    assert response.status_code == 422, response.text
