"""E2E: end-to-end flow of schedule → promote → run; cancel-after-promote.

Covers the gaps left by Task 5/6 tests:

- Promote happy path: schedule with ``delay_seconds``, wait for maturity,
  trigger the promoter directly (for test speed), then verify the worker
  runs the promoted row to ``Success``. The worker's ``create_execution``
  upserts in place when a pre-existing scheduled row is found, so the
  promoted row's PK is reused rather than colliding.
- Cancel-after-promote returns 409: once the row has been flipped past
  ``SCHEDULED`` (simulated by a direct DB UPDATE so the test does not
  depend on worker wiring), the cancel endpoint must refuse with 409.
- Validation sanity: past ``scheduled_at`` and ``sync=True + delay_seconds``
  both return 422 (redundant with contract unit tests, but one e2e pass
  per plan).

Auth cases (non-admin cross-user cancel → 403) are deferred; they need a
second authenticated user that the current fixtures don't provide cheaply.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from tests.e2e.conftest import write_and_register


pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def runnable_workflow(e2e_client, platform_admin):
    """Register a trivial workflow used across the flow tests."""
    workflow_content = '''"""E2E Scheduling Flow Workflow"""
from bifrost import workflow

@workflow(
    name="e2e_scheduling_flow_workflow",
    description="Workflow used by scheduled-execution flow E2E tests",
)
async def e2e_scheduling_flow_workflow(foo: str = "bar") -> dict:
    return {"ok": True, "foo": foo}
'''
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_scheduling_flow_workflow.py",
        workflow_content,
        "e2e_scheduling_flow_workflow",
    )
    yield {"id": result["id"], "name": result.get("name", "e2e_scheduling_flow_workflow")}

    e2e_client.delete(
        "/api/files/editor?path=e2e_scheduling_flow_workflow.py",
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


def _schedule(e2e_client, platform_admin, workflow_id: str, **extra):
    payload = {
        "workflow_id": str(workflow_id),
        "input_data": {},
        **extra,
    }
    return e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json=payload,
    )


@pytest.mark.asyncio
async def test_schedule_promote_run_terminal(
    e2e_client,
    platform_admin,
    runnable_workflow,
    db_session: AsyncSession,
    cleanup_scheduled_rows: list[UUID],
):
    """Schedule → wait for maturity → promoter → worker → Success."""
    from src.core.database import reset_db_state
    from src.jobs.rabbitmq import rabbitmq
    import src.core.redis_client as redis_module

    # The app-side DB engine, Redis singleton, and RabbitMQ connection pool
    # are cached in module globals and pin their connections to whichever
    # asyncio loop first touched them. Resetting all three forces fresh
    # clients on this loop — the promoter below uses the DB and Redis when
    # reading the matured row, and RabbitMQ when publishing it. Without the
    # pool reset, init_pools() short-circuits on a pool bound to a dead loop
    # and the publish fails with RuntimeError: Event loop is closed.
    reset_db_state()
    redis_module._redis_client = None
    rabbitmq.reset_pools()

    resp = _schedule(
        e2e_client, platform_admin, runnable_workflow["id"], delay_seconds=2
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Scheduled", body
    exec_id = UUID(body["execution_id"])
    cleanup_scheduled_rows.append(exec_id)

    # Wait past scheduled_at, then trigger the promoter directly for speed.
    await asyncio.sleep(3)
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    # The scheduler container ALSO runs promote_due_executions every 60s
    # (next_run_time=now at boot). If its tick lands inside our 3s sleep
    # above, it picks up our row via SELECT FOR UPDATE SKIP LOCKED before
    # this direct call runs, and our call returns promoted=0. That is
    # behaviorally fine — the row still gets promoted and the worker still
    # runs it — but it makes asserting `promoted >= 1` flaky. So we drop
    # the promoted-count assertion and verify the post-condition: the row
    # left SCHEDULED (regardless of which tick promoted it) and any
    # publishes our call attempted didn't fail.
    _, failures = await promote_due_executions()
    assert failures == 0, f"expected 0 publish failures, got {failures}"

    # Poll for the worker to run it to a terminal status.
    deadline = asyncio.get_event_loop().time() + 60
    row: Execution | None = None
    while asyncio.get_event_loop().time() < deadline:
        await db_session.rollback()
        row = (
            await db_session.execute(
                select(Execution).where(Execution.id == exec_id)
            )
        ).scalar_one()
        if row.status in (
            ExecutionStatus.SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.COMPLETED_WITH_ERRORS,
        ):
            break
        await asyncio.sleep(1)

    assert row is not None, "poll loop never ran"
    assert row.status == ExecutionStatus.SUCCESS, (
        f"expected Success, got {row.status} "
        f"(error_message={row.error_message!r})"
    )


@pytest.mark.asyncio
async def test_cancel_after_promote_returns_409(
    e2e_client,
    platform_admin,
    runnable_workflow,
    db_session: AsyncSession,
    cleanup_scheduled_rows: list[UUID],
):
    """Once the row has been flipped past SCHEDULED, cancel must 409.

    We simulate promotion via a direct DB UPDATE instead of invoking the
    promoter + worker. The cancel endpoint's guard is ``WHERE status =
    SCHEDULED``; any non-scheduled value proves the guard fires. Using
    direct UPDATE keeps this test hermetic from worker wiring.
    """
    # Schedule far out so the scheduler can't promote out from under us.
    resp = _schedule(
        e2e_client, platform_admin, runnable_workflow["id"], delay_seconds=600
    )
    assert resp.status_code == 200, resp.text
    exec_id = UUID(resp.json()["execution_id"])
    cleanup_scheduled_rows.append(exec_id)

    # Simulate the promoter flipping the row to PENDING.
    await db_session.execute(
        update(Execution)
        .where(Execution.id == exec_id)
        .values(status=ExecutionStatus.PENDING)
    )
    await db_session.commit()

    cancel = e2e_client.post(
        f"/api/workflows/executions/{exec_id}/cancel",
        headers=platform_admin.headers,
    )
    assert cancel.status_code == 409, cancel.text
    assert "Scheduled" in cancel.json().get("detail", ""), cancel.text


def test_validation_past_scheduled_at_returns_422(
    e2e_client, platform_admin, runnable_workflow
):
    """``scheduled_at`` in the past is a contract violation."""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    resp = _schedule(
        e2e_client,
        platform_admin,
        runnable_workflow["id"],
        scheduled_at=past.isoformat(),
    )
    assert resp.status_code == 422, resp.text


def test_validation_sync_plus_delay_returns_422(
    e2e_client, platform_admin, runnable_workflow
):
    """``sync=True`` + ``delay_seconds`` is mutually exclusive."""
    resp = _schedule(
        e2e_client,
        platform_admin,
        runnable_workflow["id"],
        delay_seconds=60,
        sync=True,
    )
    assert resp.status_code == 422, resp.text
