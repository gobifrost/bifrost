from unittest.mock import AsyncMock, patch

import pytest

from src.services.execution.async_executor import _publish_pending


@pytest.mark.asyncio
async def test_publish_pending_writes_redis_then_publishes():
    redis = AsyncMock()
    with (
        patch("src.services.execution.async_executor.get_redis_client", return_value=redis),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock()) as q,
        patch("src.services.execution.async_executor.publish_message", new=AsyncMock()) as pub,
    ):
        await _publish_pending(
            execution_id="e1",
            workflow_id="wf",
            parameters={"x": 1},
            org_id="org",
            user_id="u",
            user_name="Name",
            user_email="n@e",
            form_id=None,
            startup=None,
            api_key_id=None,
            sync=False,
            is_platform_admin=False,
            file_path=None,
        )

    redis.set_pending_execution.assert_awaited_once()
    q.assert_awaited_once_with("e1")
    pub.assert_awaited_once()
    queue_name, message = pub.await_args.args
    assert queue_name == "workflow-executions"
    assert message == {"execution_id": "e1", "workflow_id": "wf", "sync": False}


@pytest.mark.asyncio
async def test_publish_pending_includes_file_path_when_present():
    redis = AsyncMock()
    with (
        patch("src.services.execution.async_executor.get_redis_client", return_value=redis),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock()),
        patch("src.services.execution.async_executor.publish_message", new=AsyncMock()) as pub,
    ):
        await _publish_pending(
            execution_id="e1",
            workflow_id="wf",
            parameters={},
            org_id="org",
            user_id="u",
            user_name="n",
            user_email="",
            form_id=None,
            startup=None,
            api_key_id=None,
            sync=True,
            is_platform_admin=False,
            file_path="workflows/foo.py",
        )
    _, message = pub.await_args.args
    assert message["file_path"] == "workflows/foo.py"
    assert message["sync"] is True


@pytest.mark.asyncio
async def test_enqueue_system_workflow_execution_defaults_to_provider_org():
    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new=AsyncMock(return_value="exec-1"),
        ) as enqueue,
    ):
        from src.services.execution.async_executor import enqueue_system_workflow_execution

        execution_id = await enqueue_system_workflow_execution(
            workflow_id="wf-1",
            parameters={"apply": True},
            source="Event System",
        )

    assert execution_id == "exec-1"
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["org_id_override"] == "00000000-0000-0000-0000-000000000002"


@pytest.mark.asyncio
async def test_enqueue_system_workflow_execution_preserves_explicit_org():
    with (
        patch(
            "src.services.execution.async_executor.enqueue_workflow_execution",
            new=AsyncMock(return_value="exec-2"),
        ) as enqueue,
    ):
        from src.services.execution.async_executor import enqueue_system_workflow_execution

        await enqueue_system_workflow_execution(
            workflow_id="wf-1",
            parameters={},
            source="Event System",
            org_id="11111111-1111-1111-1111-111111111111",
        )

    assert enqueue.await_args.kwargs["org_id_override"] == "11111111-1111-1111-1111-111111111111"
