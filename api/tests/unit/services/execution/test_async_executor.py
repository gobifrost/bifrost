from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from src.services.audit_context import ActorContext, clear_actor, set_actor
from src.services.durable_impersonation import pending_durable_impersonation
from src.services.execution.async_executor import _publish_pending, enqueue_code_execution

ACCEPT = "src.services.execution.async_executor._accept"


def _publish_kwargs(**overrides):
    kwargs = dict(
        execution_id="e1",
        workflow_id="wf",
        parameters={},
        org_id="org",
        user_id="u",
        user_name="Name",
        user_email="n@e",
        form_id=None,
        startup=None,
        form_inputs={},
        embed={},
        api_key_id=None,
        sync=False,
        is_platform_admin=False,
        file_path=None,
        lineage=None,
    )
    kwargs.update(overrides)
    return kwargs


@pytest.mark.asyncio
async def test_publish_pending_accepts_the_context_and_minimal_message():
    with patch(ACCEPT, new=AsyncMock()) as accept:
        await _publish_pending(
            **_publish_kwargs(
                parameters={"x": 1},
                form_inputs={"field": "value"},
                embed={"ticket_id": "1001"},
            )
        )

    pending, message = accept.await_args.args
    assert pending["parameters"] == {"x": 1}
    assert pending["form_inputs"] == {"field": "value"}
    assert pending["embed"] == {"ticket_id": "1001"}
    assert pending["sync"] is False
    assert message == {"execution_id": "e1", "workflow_id": "wf", "sync": False}


@pytest.mark.asyncio
async def test_publish_pending_carries_server_actor_impersonation_across_the_queue_boundary():
    operator_id = "00000000-0000-0000-0000-0000000000bb"
    session_id = "00000000-0000-0000-0000-0000000000cc"
    token = set_actor(
        ActorContext(
            user_id=None,
            organization_id=None,
            impersonated_by_user_id=UUID(operator_id),
            impersonation_session_id=UUID(session_id),
        )
    )
    try:
        with patch(ACCEPT, new=AsyncMock()) as accept:
            await _publish_pending(**_publish_kwargs(org_id=None))
    finally:
        clear_actor(token)

    pending, _ = accept.await_args.args
    assert pending["impersonation_provenance"] == {
        "impersonated_by_user_id": operator_id,
        "impersonation_session_id": session_id,
    }


def test_pending_impersonation_provenance_rejects_incomplete_internal_values() -> None:
    with pytest.raises(ValueError, match="requires both operator and session"):
        pending_durable_impersonation(
            {"impersonated_by_user_id": "00000000-0000-0000-0000-0000000000bb"}
        )


@pytest.mark.asyncio
async def test_publish_pending_includes_file_path_when_present():
    with patch(ACCEPT, new=AsyncMock()) as accept:
        await _publish_pending(**_publish_kwargs(sync=True, file_path="workflows/foo.py"))

    _, message = accept.await_args.args
    assert message["file_path"] == "workflows/foo.py"
    assert message["sync"] is True


@pytest.mark.asyncio
async def test_publish_pending_carries_authorized_dispatch_metadata():
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
    with patch(ACCEPT, new=AsyncMock()) as accept:
        await _publish_pending(
            **_publish_kwargs(sync=True, dispatch_metadata=dispatch_metadata)
        )

    _, message = accept.await_args.args
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
async def test_enqueue_code_execution_accepts_script_context_and_code_message():
    with patch(ACCEPT, new=AsyncMock()) as accept:
        execution_id = await enqueue_code_execution(
            context=_context(),
            script_name="inline.py",
            code_base64="cHJpbnQoJ2hpJyk=",
            parameters={"x": 1},
            execution_id="exec-sync",
            sync=True,
            lineage=None,
        )

    assert execution_id == "exec-sync"
    pending, message = accept.await_args.args
    assert pending["sync"] is True
    assert pending["script_name"] == "inline.py"
    assert pending["workflow_id"] is None
    assert message == {
        "execution_id": "exec-sync",
        "code": "cHJpbnQoJ2hpJyk=",
        "script_name": "inline.py",
        "sync": True,
    }


@pytest.mark.asyncio
async def test_publish_pending_stores_the_lineage():
    lineage = {"run_user_id": "u", "started_by_user_id": "u", "root_execution_id": "e1"}
    with patch(ACCEPT, new=AsyncMock()) as accept:
        await _publish_pending(**_publish_kwargs(org_id=None, lineage=lineage))

    pending, _ = accept.await_args.args
    assert pending["lineage"] == lineage


@pytest.mark.asyncio
async def test_enqueue_workflow_execution_binds_lineage_to_the_execution():
    from src.services.execution.async_executor import enqueue_workflow_execution
    from shared.run_lineage import person_lineage

    person = "00000000-0000-0000-0000-00000000aaaa"
    context = _context()
    context.event = None
    with patch(
        "src.services.execution.async_executor._publish_pending", new=AsyncMock()
    ) as publish:
        execution_id = await enqueue_workflow_execution(
            context=context,
            workflow_id="wf",
            parameters={},
            execution_id="00000000-0000-0000-0000-00000000bbbb",
            lineage=person_lineage(person),
        )

    assert publish.await_args.kwargs["lineage"] == {
        "run_user_id": person,
        "started_by_user_id": person,
        "root_execution_id": execution_id,
    }


@pytest.mark.asyncio
async def test_enqueue_workflow_execution_allocates_a_fresh_uuid_when_unset():
    from src.services.execution.async_executor import enqueue_workflow_execution

    context = _context()
    context.event = None
    with patch(
        "src.services.execution.async_executor._publish_pending", new=AsyncMock()
    ) as publish:
        first = await enqueue_workflow_execution(
            context=context,
            workflow_id="wf",
            parameters={},
            lineage=None,
        )
        second = await enqueue_workflow_execution(
            context=context,
            workflow_id="wf",
            parameters={},
            lineage=None,
        )

    UUID(first)
    UUID(second)
    assert first != second
    assert [call.kwargs["execution_id"] for call in publish.await_args_list] == [first, second]


def test_sync_callers_never_wait_forever() -> None:
    from src.services.execution.service import (
        SYNC_RESULT_MARGIN_SECONDS,
        SYNC_WAIT_CEILING_SECONDS,
        sync_wait_seconds,
    )

    assert sync_wait_seconds(300) == 300 + SYNC_RESULT_MARGIN_SECONDS
    # 0 means "no workflow timeout", which must not become BLPOP's "forever".
    assert sync_wait_seconds(0) == SYNC_WAIT_CEILING_SECONDS
