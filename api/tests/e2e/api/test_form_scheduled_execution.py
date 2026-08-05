"""E2E: scheduling a form execution returns a SCHEDULED row tagged with form_id."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from tests.e2e.conftest import write_and_register


pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def scheduled_form_workflow(e2e_client, platform_admin):
    """Register a trivial workflow the form targets (never actually runs)."""
    workflow_content = '''"""E2E Scheduled Form Workflow"""
from bifrost import workflow

@workflow(
    name="e2e_scheduled_form_workflow",
    description="Workflow used by scheduled form-execution E2E tests",
)
async def e2e_scheduled_form_workflow(foo: str = "bar") -> dict:
    return {"ok": True, "foo": foo}
'''
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_scheduled_form_workflow.py",
        workflow_content,
        "e2e_scheduled_form_workflow",
    )
    yield {"id": result["id"], "name": result.get("name", "e2e_scheduled_form_workflow")}

    e2e_client.delete(
        "/api/files/editor?path=e2e_scheduled_form_workflow.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def scheduled_form(e2e_client, platform_admin, scheduled_form_workflow):
    """Create a form tied to the scheduled workflow."""
    response = e2e_client.post(
        "/api/forms",
        headers=platform_admin.headers,
        json={
            "name": "E2E Scheduled Form",
            "description": "Form used by scheduled-execution E2E tests",
            "workflow_id": scheduled_form_workflow["id"],
            "form_schema": {
                "fields": [
                    {"name": "foo", "type": "text", "label": "Foo", "required": False},
                ]
            },
            "access_level": "authenticated",
        },
    )
    assert response.status_code == 201, f"Create form failed: {response.text}"
    form = response.json()

    yield form

    e2e_client.delete(
        f"/api/forms/{form['id']}",
        headers=platform_admin.headers,
    )


@pytest_asyncio.fixture
async def cleanup_scheduled_rows(db_session: AsyncSession):  # type: ignore[misc]
    """Delete any Execution rows this test created after it finishes."""
    created_ids: list[UUID] = []
    yield created_ids
    if created_ids:
        await db_session.execute(
            delete(Execution).where(Execution.id.in_(created_ids))
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_form_schedule_with_delay_seconds_creates_scheduled_row(
    e2e_client,
    platform_admin,
    scheduled_form,
    scheduled_form_workflow,
    db_session: AsyncSession,
    cleanup_scheduled_rows: list[UUID],
):
    before = datetime.now(timezone.utc)

    resp = e2e_client.post(
        f"/api/forms/{scheduled_form['id']}/submissions",
        headers=platform_admin.headers,
        json={
            "form_data": {"foo": "baz"},
            "delay_seconds": 300,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Scheduled"
    assert body["scheduled_at"] is not None
    assert body["execution_id"] is not None

    exec_id = UUID(body["execution_id"])
    cleanup_scheduled_rows.append(exec_id)

    row = (
        await db_session.execute(
            select(Execution).where(Execution.id == exec_id)
        )
    ).scalar_one()
    assert row.status == ExecutionStatus.SCHEDULED
    assert row.scheduled_at is not None
    assert row.form_id == UUID(scheduled_form["id"])
    assert row.workflow_id == UUID(scheduled_form_workflow["id"])
    assert row.parameters == {"foo": "baz"}
    assert row.started_at is None
    assert row.completed_at is None

    delta = row.scheduled_at - before
    assert timedelta(seconds=290) <= delta <= timedelta(seconds=330)


@pytest.mark.asyncio
async def test_form_schedule_past_scheduled_at_is_422(
    e2e_client,
    platform_admin,
    scheduled_form,
):
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    resp = e2e_client.post(
        f"/api/forms/{scheduled_form['id']}/submissions",
        headers=platform_admin.headers,
        json={
            "form_data": {},
            "scheduled_at": past.isoformat(),
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_form_schedule_both_fields_is_422(
    e2e_client,
    platform_admin,
    scheduled_form,
):
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    resp = e2e_client.post(
        f"/api/forms/{scheduled_form['id']}/submissions",
        headers=platform_admin.headers,
        json={
            "form_data": {},
            "scheduled_at": future.isoformat(),
            "delay_seconds": 300,
        },
    )
    assert resp.status_code == 422, resp.text
