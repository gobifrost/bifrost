from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.enums import ExecutionStatus
from src.services.execution.module_loader import WorkflowMetadata
from src.services.execution.service import _enqueue_workflow_async, run_workflow


@pytest.mark.asyncio
async def test_run_workflow_reuses_validated_timeout_for_sync_wait() -> None:
    metadata = WorkflowMetadata(name="fast", timeout_seconds=123)
    metadata.id = "workflow-1"
    context = MagicMock()

    with (
        patch(
            "src.services.execution.service.get_workflow_metadata_only",
            new_callable=AsyncMock,
            return_value=metadata,
        ) as get_metadata,
        patch(
            "src.services.execution.service._enqueue_workflow_async",
            new_callable=AsyncMock,
        ) as enqueue,
    ):
        await run_workflow(context, "workflow-1", sync=True, lineage=None)

    get_metadata.assert_awaited_once_with("workflow-1")
    enqueue.assert_awaited_once_with(
        context=context,
        workflow_id="workflow-1",
        workflow_name="fast",
        parameters={},
        form_id=None,
        sync=True,
        timeout_seconds=123,
        dispatch_metadata=None,
        trusted_app_context=False,
        lineage=None,
    )


@pytest.mark.asyncio
async def test_bounded_sync_wait_returns_pending_when_result_is_not_ready() -> None:
    context = MagicMock(execution_id="accepted-execution")
    redis_client = MagicMock(wait_for_result=AsyncMock(return_value=None))

    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new_callable=AsyncMock,
            return_value="accepted-execution",
        ) as enqueue,
        patch(
            "src.core.redis_client.get_redis_client",
            return_value=redis_client,
        ),
    ):
        response = await _enqueue_workflow_async(
            context=context,
            workflow_id="workflow-1",
            workflow_name="fast",
            parameters={},
            sync=True,
            timeout_seconds=120,
            result_wait_seconds=30,
            lineage=None,
        )

    enqueue.assert_awaited_once()
    redis_client.wait_for_result.assert_awaited_once_with(
        "accepted-execution", timeout_seconds=30
    )
    assert response.execution_id == "accepted-execution"
    assert response.status is ExecutionStatus.PENDING
    assert response.error is None


@pytest.mark.asyncio
async def test_bounded_sync_wait_returns_completed_worker_result() -> None:
    context = MagicMock(execution_id="execution-1")
    redis_client = MagicMock(
        wait_for_result=AsyncMock(
            return_value={
                "status": ExecutionStatus.SUCCESS.value,
                "result": {"ticket_id": 7},
                "duration_ms": 42,
            }
        )
    )

    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new_callable=AsyncMock,
            return_value="execution-1",
        ),
        patch(
            "src.core.redis_client.get_redis_client",
            return_value=redis_client,
        ),
    ):
        response = await _enqueue_workflow_async(
            context=context,
            workflow_id="workflow-1",
            workflow_name="fast",
            parameters={},
            sync=True,
            timeout_seconds=120,
            result_wait_seconds=30,
            lineage=None,
        )

    assert response.status is ExecutionStatus.SUCCESS
    assert response.result == {"ticket_id": 7}
    assert response.duration_ms == 42


@pytest.mark.asyncio
async def test_default_sync_wait_returns_timeout_when_result_is_not_ready() -> None:
    context = MagicMock(execution_id="execution-1")
    redis_client = MagicMock(wait_for_result=AsyncMock(return_value=None))

    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new_callable=AsyncMock,
            return_value="execution-1",
        ),
        patch(
            "src.core.redis_client.get_redis_client",
            return_value=redis_client,
        ),
    ):
        response = await _enqueue_workflow_async(
            context=context,
            workflow_id="workflow-1",
            workflow_name="fast",
            parameters={},
            sync=True,
            timeout_seconds=120,
            lineage=None,
        )

    redis_client.wait_for_result.assert_awaited_once_with(
        "execution-1", timeout_seconds=180
    )
    assert response.status is ExecutionStatus.TIMEOUT
    assert response.error == "Execution timed out waiting for result"


@pytest.mark.asyncio
@pytest.mark.parametrize("result_wait_seconds", [0, -1])
async def test_nonpositive_result_wait_does_not_enqueue(result_wait_seconds: int) -> None:
    context = MagicMock(execution_id="execution-1")

    with patch(
        "src.services.execution.async_executor.enqueue_workflow_execution",
        new_callable=AsyncMock,
    ) as enqueue:
        with pytest.raises(ValueError, match="result_wait_seconds must be positive"):
            await _enqueue_workflow_async(
                context=context,
                workflow_id="workflow-1",
                workflow_name="fast",
                parameters={},
                result_wait_seconds=result_wait_seconds,
                lineage=None,
            )

    enqueue.assert_not_awaited()


def test_request_only_publishes_nonterminal_execution_state() -> None:
    from shared.sdk_workflow_execution import (
        is_uuid_workflow_ref,
        should_publish_request_execution_update,
    )

    assert should_publish_request_execution_update(ExecutionStatus.PENDING)
    assert should_publish_request_execution_update(ExecutionStatus.RUNNING)
    assert not should_publish_request_execution_update(ExecutionStatus.SUCCESS)
    assert not should_publish_request_execution_update(ExecutionStatus.FAILED)
    assert not should_publish_request_execution_update(ExecutionStatus.TIMEOUT)

    assert is_uuid_workflow_ref("84cc0faf-36a2-5873-b9a9-d5484d5db5d8")
    assert not is_uuid_workflow_ref("workflows/halo.py::list_tickets")


@pytest.mark.asyncio
async def test_solution_global_check_reads_only_the_scalar_flag() -> None:
    from src.services.solution_scope import solution_allows_global

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = True
    db.execute.return_value = result

    assert await solution_allows_global(db, uuid4()) is True
    db.get.assert_not_awaited()
    db.execute.assert_awaited_once()
