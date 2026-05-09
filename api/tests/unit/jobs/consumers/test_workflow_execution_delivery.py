"""Delivery outcome tests for the workflow execution consumer."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.jobs.rabbitmq import (
    DomainFailureHandled,
    DuplicateMessage,
    MalformedMessage,
    RetryableConsumerError,
)
from src.models.enums import ExecutionStatus


def make_consumer() -> WorkflowExecutionConsumer:
    """Create a consumer without wiring real Redis, RabbitMQ, or process pool clients."""
    with patch.object(WorkflowExecutionConsumer, "__init__", lambda self: None):
        consumer = WorkflowExecutionConsumer()

    consumer._redis_client = AsyncMock()
    return consumer


def pending_context() -> dict[str, object]:
    return {
        "parameters": {},
        "org_id": None,
        "user_id": str(uuid4()),
        "user_name": "Test User",
        "user_email": "test@example.com",
    }


@pytest.mark.asyncio
async def test_process_message_rejects_missing_execution_id() -> None:
    consumer = make_consumer()
    consumer._redis_client.get_pending_execution.return_value = None

    with patch(
        "src.services.execution.queue_tracker.remove_from_queue",
        new_callable=AsyncMock,
    ) as remove_from_queue:
        with pytest.raises(MalformedMessage, match="execution_id"):
            await consumer.process_message({})

    remove_from_queue.assert_not_called()
    consumer._redis_client.get_pending_execution.assert_not_called()


@pytest.mark.asyncio
async def test_process_message_treats_existing_execution_without_pending_context_as_duplicate() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    consumer._redis_client.get_pending_execution.return_value = None
    consumer._get_existing_execution_status = AsyncMock(return_value="Success")  # type: ignore[attr-defined]

    with patch(
        "src.services.execution.queue_tracker.remove_from_queue",
        new_callable=AsyncMock,
    ) as remove_from_queue:
        with pytest.raises(DuplicateMessage, match="already exists"):
            await consumer.process_message({"execution_id": execution_id})

    remove_from_queue.assert_awaited_once_with(execution_id)
    consumer._get_existing_execution_status.assert_awaited_once_with(execution_id)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_process_message_retries_missing_pending_context() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    consumer._redis_client.get_pending_execution.return_value = None
    consumer._redis_client.push_result = AsyncMock()
    consumer._get_existing_execution_status = AsyncMock(return_value=None)  # type: ignore[attr-defined]

    with patch(
        "src.services.execution.queue_tracker.remove_from_queue",
        new_callable=AsyncMock,
    ):
        with pytest.raises(RetryableConsumerError, match="pending execution"):
            await consumer.process_message({"execution_id": execution_id, "sync": True})

    consumer._redis_client.push_result.assert_not_called()


@pytest.mark.asyncio
async def test_process_message_retries_pool_admission_memory_pressure_without_deleting_pending() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    consumer._pool = AsyncMock()
    consumer._pool.route_execution = AsyncMock(side_effect=MemoryError("limit reached"))
    consumer._redis_client.get_pending_execution.return_value = pending_context()
    consumer._redis_client.delete_pending_execution = AsyncMock()

    with (
        patch(
            "src.services.execution.queue_tracker.remove_from_queue",
            new_callable=AsyncMock,
        ),
        patch("src.repositories.executions.create_execution", new_callable=AsyncMock),
        patch("src.repositories.executions.update_execution", new_callable=AsyncMock) as update_execution,
        patch(
            "src.jobs.consumers.workflow_execution.publish_execution_update",
            new_callable=AsyncMock,
        ),
        patch(
            "src.jobs.consumers.workflow_execution.publish_history_update",
            new_callable=AsyncMock,
        ),
    ):
        with pytest.raises(RetryableConsumerError, match="admission"):
            await consumer.process_message(
                {
                    "execution_id": execution_id,
                    "code": "cHJpbnQoJ2hpJyk=",
                    "script_name": "inline.py",
                }
            )

    consumer._redis_client.delete_pending_execution.assert_not_called()
    update_execution.assert_awaited_once()
    assert update_execution.await_args is not None
    assert update_execution.await_args.kwargs["execution_id"] == execution_id
    assert update_execution.await_args.kwargs["status"] == ExecutionStatus.PENDING


@pytest.mark.asyncio
async def test_process_message_acknowledges_recorded_setup_failure_as_domain_handled() -> None:
    consumer = make_consumer()
    execution_id = str(uuid4())
    consumer._pool = AsyncMock()
    consumer._pool.route_execution = AsyncMock(side_effect=ValueError("bad setup"))
    consumer._redis_client.get_pending_execution.return_value = pending_context()
    consumer._redis_client.delete_pending_execution = AsyncMock()
    consumer._redis_client.push_result = AsyncMock()

    with (
        patch(
            "src.services.execution.queue_tracker.remove_from_queue",
            new_callable=AsyncMock,
        ),
        patch("src.repositories.executions.create_execution", new_callable=AsyncMock),
        patch("src.repositories.executions.update_execution", new_callable=AsyncMock) as update_execution,
        patch(
            "src.jobs.consumers.workflow_execution.publish_execution_update",
            new_callable=AsyncMock,
        ),
        patch(
            "src.jobs.consumers.workflow_execution.publish_history_update",
            new_callable=AsyncMock,
        ),
    ):
        with pytest.raises(DomainFailureHandled, match="workflow setup failure"):
            await consumer.process_message(
                {
                    "execution_id": execution_id,
                    "code": "cHJpbnQoJ2hpJyk=",
                    "script_name": "inline.py",
                    "sync": True,
                }
            )

    update_execution.assert_awaited_once()
    consumer._redis_client.delete_pending_execution.assert_awaited_once_with(execution_id)
    consumer._redis_client.push_result.assert_awaited_once_with(
        execution_id=execution_id,
        status="Failed",
        error="bad setup",
        error_type="ValueError",
        duration_ms=pytest.approx(0, abs=1000),
    )
