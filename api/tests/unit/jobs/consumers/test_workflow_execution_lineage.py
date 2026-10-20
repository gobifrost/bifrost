"""The consumer writes the lineage its pending record carries onto the execution row."""

from unittest.mock import AsyncMock, patch

import pytest

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

MODULE = "src.jobs.consumers.workflow_execution"
EXECUTION_ID = "00000000-0000-0000-0000-000000000001"
RUN_USER_ID = "00000000-0000-0000-0000-0000000000aa"


def _pending(**extra) -> dict:
    return {
        "execution_id": EXECUTION_ID,
        "workflow_id": None,
        "script_name": "inline",
        "parameters": {},
        "org_id": None,
        "user_id": RUN_USER_ID,
        "user_name": "Person",
        "user_email": "p@example.com",
        "cancelled": True,
        **extra,
    }


async def _created_with(pending: dict) -> dict:
    with (
        patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None),
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(f"{MODULE}.update_execution", new=AsyncMock()),
        patch(f"{MODULE}.publish_execution_update", new=AsyncMock()),
        patch(f"{MODULE}.publish_history_update", new=AsyncMock()),
        patch(
            "shared.run_cancellation.cancel_redis_queued_run_if_user_deleted",
            new=AsyncMock(return_value=False),
        ),
        patch("src.services.execution.queue_tracker.remove_from_queue", new=AsyncMock()),
    ):
        consumer = WorkflowExecutionConsumer()
        consumer._redis_client = AsyncMock()
        consumer._redis_client.get_pending_execution.return_value = pending
        await consumer.process_message(
            {"execution_id": EXECUTION_ID, "code": "eA==", "script_name": "inline"}
        )
    assert create.await_args is not None
    return dict(create.await_args.kwargs)


@pytest.mark.asyncio
async def test_row_gets_the_pending_lineage() -> None:
    lineage = {
        "run_user_id": RUN_USER_ID,
        "started_by_user_id": RUN_USER_ID,
        "root_execution_id": EXECUTION_ID,
    }

    assert (await _created_with(_pending(lineage=lineage)))["lineage"] == lineage


@pytest.mark.asyncio
async def test_row_gets_the_pending_impersonation_provenance() -> None:
    operator_id = "00000000-0000-0000-0000-0000000000bb"
    session_id = "00000000-0000-0000-0000-0000000000cc"

    created = await _created_with(
        _pending(
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
async def test_pending_record_written_before_lineage_existed_is_still_consumed() -> None:
    assert (await _created_with(_pending()))["lineage"] is None


@pytest.mark.asyncio
async def test_cancelled_promoted_run_is_discarded_before_worker_dispatch() -> None:
    class _DbContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    session = AsyncMock()
    pending = _pending(cancelled=False)
    with (
        patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None),
        patch(f"{MODULE}.get_db_context", return_value=_DbContext()),
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch("shared.run_cancellation.claim_pending_run_for_dispatch", new=AsyncMock(return_value=False)),
        patch("src.services.execution.queue_tracker.remove_from_queue", new=AsyncMock()),
    ):
        consumer = WorkflowExecutionConsumer()
        consumer._redis_client = AsyncMock()
        consumer._redis_client.get_pending_execution.return_value = pending
        await consumer.process_message(
            {
                "execution_id": EXECUTION_ID,
                "code": "eA==",
                "script_name": "inline",
                "execution_record_exists": True,
            }
        )

    create.assert_not_awaited()
    session.commit.assert_awaited_once()
    consumer._redis_client.delete_pending_execution.assert_awaited_once_with(EXECUTION_ID)


@pytest.mark.asyncio
async def test_redis_queued_run_for_a_deleted_user_is_discarded_before_dispatch() -> None:
    class _DbContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    session = AsyncMock()
    pending = _pending(
        cancelled=False,
        lineage={
            "run_user_id": RUN_USER_ID,
            "started_by_user_id": RUN_USER_ID,
            "root_execution_id": EXECUTION_ID,
        },
    )
    with (
        patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None),
        patch(f"{MODULE}.get_db_context", return_value=_DbContext()),
        patch(f"{MODULE}.create_execution", new=AsyncMock()) as create,
        patch(
            "shared.run_cancellation.cancel_redis_queued_run_if_user_deleted",
            new=AsyncMock(return_value=True),
        ),
        patch("src.services.execution.queue_tracker.remove_from_queue", new=AsyncMock()),
    ):
        consumer = WorkflowExecutionConsumer()
        consumer._redis_client = AsyncMock()
        consumer._redis_client.get_pending_execution.return_value = pending
        await consumer.process_message(
            {"execution_id": EXECUTION_ID, "code": "eA==", "script_name": "inline"}
        )

    create.assert_not_awaited()
    session.commit.assert_awaited_once()
    consumer._redis_client.delete_pending_execution.assert_awaited_once_with(EXECUTION_ID)
