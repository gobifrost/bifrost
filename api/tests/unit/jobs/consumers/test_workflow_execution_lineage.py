"""The consumer writes the lineage its claimed run carries onto the execution row."""

from unittest.mock import AsyncMock, patch

import pytest

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

MODULE = "src.jobs.consumers.workflow_execution"
EXECUTION_ID = "00000000-0000-0000-0000-000000000001"
RUN_USER_ID = "00000000-0000-0000-0000-0000000000aa"
MESSAGE = {"execution_id": EXECUTION_ID, "code": "eA==", "script_name": "inline"}


def _claimed(**extra) -> dict:
    return {
        "execution_id": EXECUTION_ID,
        "workflow_id": None,
        "script_name": "inline",
        "parameters": {},
        "org_id": None,
        "user_id": RUN_USER_ID,
        "user_name": "Person",
        "user_email": "p@example.com",
        "lineage": None,
        **extra,
    }


def _consumer() -> WorkflowExecutionConsumer:
    consumer = WorkflowExecutionConsumer.__new__(WorkflowExecutionConsumer)
    consumer._redis_client = AsyncMock()
    consumer._pool = AsyncMock()
    return consumer


async def _created_with(claimed: dict, *, queue_error: Exception | None = None) -> dict:
    with (
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(f"{MODULE}.publish_execution_update", new=AsyncMock()),
        patch(f"{MODULE}.publish_history_update", new=AsyncMock()),
        patch("src.services.execution.queue_tracker.publish_queue_positions", new=AsyncMock(side_effect=queue_error)),
    ):
        consumer = _consumer()
        await consumer.process_message(MESSAGE, claimed)  # type: ignore[arg-type]
    consumer._pool.route_execution.assert_awaited_once()
    assert create.await_args is not None
    return dict(create.await_args.kwargs)


@pytest.mark.asyncio
async def test_row_gets_the_claimed_lineage() -> None:
    lineage = {
        "run_user_id": RUN_USER_ID,
        "started_by_user_id": RUN_USER_ID,
        "root_execution_id": EXECUTION_ID,
    }

    assert (await _created_with(_claimed(lineage=lineage)))["lineage"] == lineage


@pytest.mark.asyncio
async def test_row_gets_the_claimed_impersonation_provenance() -> None:
    operator_id = "00000000-0000-0000-0000-0000000000bb"
    session_id = "00000000-0000-0000-0000-0000000000cc"

    created = await _created_with(
        _claimed(
            impersonation_provenance={
                "impersonated_by_user_id": operator_id,
                "impersonation_session_id": session_id,
            }
        )
    )

    assert created["impersonation_provenance"] == {
        "impersonated_by_user_id": operator_id,
        "impersonation_session_id": session_id,
    }


@pytest.mark.asyncio
async def test_a_run_that_moved_on_is_discarded_and_wakes_its_sync_caller() -> None:
    class _DbContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    session = AsyncMock()
    with (
        patch(f"{MODULE}.get_db_context", return_value=_DbContext()),
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(
            "src.services.execution.workflow_dispatch.notify_discarded_sync_run",
            new=AsyncMock(),
        ) as notify,
        patch("src.services.execution.queue_tracker.publish_queue_positions", new=AsyncMock()),
    ):
        consumer = _consumer()
        await consumer.process_message({**MESSAGE, "sync": True}, None)

    create.assert_not_awaited()
    consumer._pool.route_execution.assert_not_awaited()
    notify.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("queue unavailable"), ConnectionError("queue unavailable")])
async def test_ui_queue_tracking_failure_does_not_abandon_a_claimed_workflow(error) -> None:
    created = await _created_with(_claimed(), queue_error=error)

    assert created["execution_id"] == EXECUTION_ID
