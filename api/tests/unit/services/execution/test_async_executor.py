from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.execution.async_executor import _publish_pending, enqueue_code_execution


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
    assert message == {
        "execution_id": "e1",
        "workflow_id": "wf",
        "sync": False,
        "execution_record_exists": False,
    }


@pytest.mark.asyncio
async def test_publish_pending_includes_file_path_when_present():
    redis = AsyncMock()
    with (
        patch("src.services.execution.async_executor.get_redis_client", return_value=redis),
        patch("src.services.execution.async_executor.add_to_queue", new=AsyncMock()) as q,
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
    q.assert_not_awaited()
    assert message["file_path"] == "workflows/foo.py"
    assert message["sync"] is True


@pytest.mark.asyncio
async def test_publish_pending_carries_authorized_dispatch_metadata():
    redis = AsyncMock()
    dispatch_metadata = {
        "name": "solution_workflow",
        "function_name": "run",
        "path": "functions/run.py",
        "timeout_seconds": 60,
        "time_saved": 0,
        "value": 0.0,
        "execution_mode": "sync",
        "organization_id": None,
        "solution_id": "solution-1",
        "can_access_global_repo": True,
        "type": "workflow",
        "cache_ttl_seconds": 0,
    }
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client",
            return_value=redis,
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue",
            new=AsyncMock(),
        ),
        patch(
            "src.services.execution.async_executor.publish_message",
            new=AsyncMock(),
        ) as publish,
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
            file_path=None,
            dispatch_metadata=dispatch_metadata,
        )

    _, message = publish.await_args.args
    assert message["dispatch_metadata"] == dispatch_metadata


def _context():
    return SimpleNamespace(
        org_id="org",
        user_id="user",
        name="User",
        email="user@example.com",
        startup=None,
        form_inputs={},
        embed={},
        is_platform_admin=False,
        artifact_workspace_id="workspace-1",
    )


@pytest.mark.asyncio
async def test_enqueue_code_execution_sync_skips_ui_queue_tracking():
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client",
            return_value=redis,
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue",
            new=AsyncMock(),
        ) as add,
        patch(
            "src.services.execution.async_executor.publish_message",
            new=AsyncMock(),
        ) as publish,
    ):
        execution_id = await enqueue_code_execution(
            context=_context(),
            script_name="inline.py",
            code_base64="cHJpbnQoJ2hpJyk=",
            parameters={"x": 1},
            execution_id="exec-sync",
            sync=True,
        )

    assert execution_id == "exec-sync"
    redis.set_pending_execution.assert_awaited_once()
    assert redis.set_pending_execution.await_args.kwargs["sync"] is True
    assert redis.set_pending_execution.await_args.kwargs["script_name"] == "inline.py"
    add.assert_not_awaited()
    publish.assert_awaited_once_with(
        "workflow-executions",
        {
            "execution_id": "exec-sync",
            "code": "cHJpbnQoJ2hpJyk=",
            "script_name": "inline.py",
            "sync": True,
        },
    )


@pytest.mark.asyncio
async def test_enqueue_code_execution_async_tracks_ui_queue():
    redis = AsyncMock()
    with (
        patch(
            "src.services.execution.async_executor.get_redis_client",
            return_value=redis,
        ),
        patch(
            "src.services.execution.async_executor.add_to_queue",
            new=AsyncMock(),
        ) as add,
        patch(
            "src.services.execution.async_executor.publish_message",
            new=AsyncMock(),
        ) as publish,
    ):
        execution_id = await enqueue_code_execution(
            context=_context(),
            script_name="inline.py",
            code_base64="cHJpbnQoJ2hpJyk=",
            parameters={"x": 1},
            execution_id="exec-async",
            sync=False,
        )

    assert execution_id == "exec-async"
    redis.set_pending_execution.assert_awaited_once()
    assert redis.set_pending_execution.await_args.kwargs["sync"] is False
    add.assert_awaited_once_with("exec-async")
    publish.assert_awaited_once()
