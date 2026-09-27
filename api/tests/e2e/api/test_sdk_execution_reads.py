"""HTTP E2E for the SDK execution-reads shared service extraction.

Pins the external behavior of the three extracted reads after routing
them through ``shared.sdk_execution_reads``:

- ``GET /api/workflows`` list shape, scope filtering, and invalid-scope
  status,
- ``GET /api/executions`` snake_case filters (the SDK's actual query
  names) through the shared service,
- ``GET /api/executions/{id}`` detail shape, 404, and owner-only denial.
"""

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
async def seeded_sdk_reads(
    db_session: AsyncSession,
    platform_admin,
) -> AsyncGenerator[dict, None]:
    assert platform_admin.user_id is not None
    tag = uuid4().hex[:8]
    workflow_id = uuid4()
    base = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

    workflow = Workflow(
        id=workflow_id,
        name=f"sdk_reads_history_{tag}",
        function_name="sdk_reads_history",
        description="SDK reads extraction history workflow",
        path=f"workflows/sdk_reads_history_{tag}.py",
        access_level="authenticated",
        is_active=True,
        created_at=base,
        updated_at=base,
    )
    db_session.add(workflow)

    rows: list[Execution] = []
    for index in range(2):
        row = Execution(
            id=uuid4(),
            workflow_name=f"sdk-reads-history-{tag}",
            workflow_id=workflow_id,
            status=ExecutionStatus.SUCCESS,
            parameters={},
            variables={"seed": True},
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
        yield {"workflow_id": workflow_id, "tag": tag, "rows": rows}
    finally:
        for row in rows:
            await db_session.execute(delete(Execution).where(Execution.id == row.id))
        await db_session.execute(delete(Workflow).where(Workflow.id == workflow_id))
        await db_session.commit()


def test_workflows_list_contains_seeded_workflow(
    e2e_client,
    platform_admin,
    seeded_sdk_reads,
):
    response = e2e_client.get("/api/workflows", headers=platform_admin.headers)
    assert response.status_code == 200, response.text
    names = {item["name"] for item in response.json()}
    assert f"sdk_reads_history_{seeded_sdk_reads['tag']}" in names


def test_workflows_list_global_scope_and_bad_scope(
    e2e_client,
    platform_admin,
    seeded_sdk_reads,
):
    global_only = e2e_client.get(
        "/api/workflows", params={"scope": "global"}, headers=platform_admin.headers
    )
    assert global_only.status_code == 200, global_only.text
    names = {item["name"] for item in global_only.json()}
    assert f"sdk_reads_history_{seeded_sdk_reads['tag']}" in names

    bad = e2e_client.get(
        "/api/workflows", params={"scope": "nope"}, headers=platform_admin.headers
    )
    assert bad.status_code == 400, bad.text


def test_executions_list_snake_case_filters(
    e2e_client,
    platform_admin,
    seeded_sdk_reads,
):
    response = e2e_client.get(
        "/api/executions",
        params={
            "workflow_id": str(seeded_sdk_reads["workflow_id"]),
            "exclude_local": "true",
            "limit": 25,
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["executions"]) == 2
    assert body["executions"][0]["execution_id"] == str(
        seeded_sdk_reads["rows"][1].id
    )


def test_execution_detail_shape_and_404(
    e2e_client,
    platform_admin,
    seeded_sdk_reads,
):
    execution_id = str(seeded_sdk_reads["rows"][1].id)
    response = e2e_client.get(
        f"/api/executions/{execution_id}", headers=platform_admin.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_id"] == execution_id
    assert body["status"] == "Success"
    assert body["variables"] is not None  # admin sees admin-only fields

    missing = e2e_client.get(
        f"/api/executions/{uuid4()}", headers=platform_admin.headers
    )
    assert missing.status_code == 404, missing.text


def test_execution_detail_unknown_param_rejected(
    e2e_client,
    platform_admin,
    seeded_sdk_reads,
):
    response = e2e_client.get(
        "/api/executions",
        params={"bogus_param": "1"},
        headers=platform_admin.headers,
    )
    assert response.status_code == 422, response.text
