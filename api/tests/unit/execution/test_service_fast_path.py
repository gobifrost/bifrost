from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.enums import ExecutionStatus
from src.services.execution.module_loader import WorkflowMetadata
from src.services.execution.service import run_workflow


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
        await run_workflow(context, "workflow-1", sync=True)

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
    )


def test_request_only_publishes_nonterminal_execution_state() -> None:
    from src.routers.workflows import (
        _is_uuid_workflow_ref,
        _should_publish_request_execution_update,
    )

    assert _should_publish_request_execution_update(ExecutionStatus.PENDING)
    assert _should_publish_request_execution_update(ExecutionStatus.RUNNING)
    assert not _should_publish_request_execution_update(ExecutionStatus.SUCCESS)
    assert not _should_publish_request_execution_update(ExecutionStatus.FAILED)
    assert not _should_publish_request_execution_update(ExecutionStatus.TIMEOUT)

    assert _is_uuid_workflow_ref("84cc0faf-36a2-5873-b9a9-d5484d5db5d8")
    assert not _is_uuid_workflow_ref("workflows/halo.py::list_tickets")


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
