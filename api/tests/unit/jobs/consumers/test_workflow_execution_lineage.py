"""The consumer writes the lineage its pending record carries onto the execution row."""

from unittest.mock import AsyncMock, patch

import pytest

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

MODULE = "src.jobs.consumers.workflow_execution"


def _pending(**extra) -> dict:
    return {
        "execution_id": "e1",
        "workflow_id": None,
        "script_name": "inline",
        "parameters": {},
        "org_id": None,
        "user_id": "00000000-0000-0000-0000-00000000aaaa",
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
        patch("src.services.execution.queue_tracker.remove_from_queue", new=AsyncMock()),
    ):
        consumer = WorkflowExecutionConsumer()
        consumer._redis_client = AsyncMock()
        consumer._redis_client.get_pending_execution.return_value = pending
        await consumer.process_message({"execution_id": "e1", "code": "eA==", "script_name": "inline"})
    assert create.await_args is not None
    return dict(create.await_args.kwargs)


@pytest.mark.asyncio
async def test_row_gets_the_pending_lineage() -> None:
    lineage = {"run_user_id": "u", "started_by_user_id": "u", "root_execution_id": "e1"}

    assert (await _created_with(_pending(lineage=lineage)))["lineage"] == lineage


@pytest.mark.asyncio
async def test_pending_record_written_before_lineage_existed_is_still_consumed() -> None:
    assert (await _created_with(_pending()))["lineage"] is None
