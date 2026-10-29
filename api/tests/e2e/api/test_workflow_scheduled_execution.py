"""E2E: scheduling a workflow returns a Scheduled row without enqueueing."""
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
def scheduled_workflow(e2e_client, platform_admin):
    """Register a trivial workflow to be scheduled (never actually runs)."""
    workflow_content = '''"""E2E Scheduled Execution Workflow"""
from bifrost import workflow

@workflow(
    name="e2e_scheduled_workflow",
    description="Workflow used by scheduled-execution E2E tests",
)
async def e2e_scheduled_workflow(foo: str = "bar") -> dict:
    return {"ok": True, "foo": foo}
'''
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_scheduled_workflow.py",
        workflow_content,
        "e2e_scheduled_workflow",
    )
    yield {"id": result["id"], "name": result.get("name", "e2e_scheduled_workflow")}

    e2e_client.delete(
        "/api/files/editor?path=e2e_scheduled_workflow.py",
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
async def test_schedule_with_scheduled_at_returns_scheduled_status(
    e2e_client,
    platform_admin,
    scheduled_workflow,
    db_session: AsyncSession,
    cleanup_scheduled_rows: list[UUID],
):
    run_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={
            "workflow_id": scheduled_workflow["id"],
            "input_data": {"foo": "bar"},
            "scheduled_at": run_at.isoformat(),
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
    assert row.parameters == {"foo": "bar"}
    assert row.workflow_id == UUID(scheduled_workflow["id"])


@pytest.mark.asyncio
async def test_schedule_with_delay_seconds_normalizes_to_scheduled_at(
    e2e_client,
    platform_admin,
    scheduled_workflow,
    db_session: AsyncSession,
    cleanup_scheduled_rows: list[UUID],
):
    before = datetime.now(timezone.utc)

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={
            "workflow_id": scheduled_workflow["id"],
            "input_data": {},
            "delay_seconds": 300,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Scheduled"

    exec_id = UUID(body["execution_id"])
    cleanup_scheduled_rows.append(exec_id)

    row = (
        await db_session.execute(
            select(Execution).where(Execution.id == exec_id)
        )
    ).scalar_one()

    # 300s delay resolved to an absolute timestamp in the near future.
    assert row.scheduled_at is not None
    delta = row.scheduled_at - before
    assert timedelta(seconds=290) <= delta <= timedelta(seconds=330)


@pytest.mark.asyncio
async def test_schedule_skips_queue(
    e2e_client,
    platform_admin,
    scheduled_workflow,
    db_session: AsyncSession,
    cleanup_scheduled_rows: list[UUID],
):
    """A scheduled request must not produce a queued or running execution.

    The API under test runs in a separate process (Docker), so we cannot
    observe its dispatch in-process. Instead we verify the post-condition:
    the Execution row is SCHEDULED, has no started_at, and does not transition
    within a short poll window (which would happen if it had been queued).
    """
    run_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={
            "workflow_id": scheduled_workflow["id"],
            "input_data": {},
            "scheduled_at": run_at.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Scheduled"

    exec_id = UUID(body["execution_id"])
    cleanup_scheduled_rows.append(exec_id)

    # Give the worker a beat to pick up anything that might have been queued
    # by mistake. If the row had been dispatched, an executor would
    # flip status to RUNNING/SUCCESS/FAILED and set started_at.
    import asyncio
    await asyncio.sleep(2.0)

    # Re-fetch (fresh read — rollback from the earlier fixture session would
    # hide nothing since we committed on the router side).
    await db_session.rollback()
    row = (
        await db_session.execute(
            select(Execution).where(Execution.id == exec_id)
        )
    ).scalar_one()
    assert row.status == ExecutionStatus.SCHEDULED, (
        f"Expected SCHEDULED, got {row.status} — was the row enqueued?"
    )
    assert row.started_at is None, "Scheduled row should not have started"
    assert row.completed_at is None, "Scheduled row should not have completed"
