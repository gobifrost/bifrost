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
            form_inputs={"field": "value"},
            embed={"ticket_id": "1001"},
            api_key_id=None,
            sync=False,
            is_platform_admin=False,
            file_path=None,
        )

    redis.set_pending_execution.assert_awaited_once()
    assert redis.set_pending_execution.await_args.kwargs["form_inputs"] == {
        "field": "value"
    }
    assert redis.set_pending_execution.await_args.kwargs["embed"] == {
        "ticket_id": "1001"
    }
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
            form_inputs={},
            embed={},
            api_key_id=None,
            sync=True,
            is_platform_admin=False,
            file_path="workflows/foo.py",
        )
    _, message = pub.await_args.args
    assert message["file_path"] == "workflows/foo.py"
    assert message["sync"] is True
