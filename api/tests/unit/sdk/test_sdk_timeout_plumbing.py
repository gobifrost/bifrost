"""SDK wait deadlines and unrelated HTTP timeout plumbing."""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _agents_module():
    if "bifrost.agents" not in sys.modules:
        importlib.import_module("bifrost.agents")
    return sys.modules["bifrost.agents"]


def _knowledge_module():
    if "bifrost.knowledge" not in sys.modules:
        importlib.import_module("bifrost.knowledge")
    return sys.modules["bifrost.knowledge"]


@pytest.mark.asyncio
async def test_agents_run_waits_through_short_status_requests(monkeypatch):
    mod = _agents_module()
    run = MagicMock(status="running")
    completed = MagicMock(status="completed", output={"text": "ok"})
    monkeypatch.setattr(mod.agents, "enqueue", AsyncMock(return_value=MagicMock(run_id="run-1")))
    monkeypatch.setattr(mod.agents, "get_run", AsyncMock(side_effect=[run, completed]))
    sleep = AsyncMock()
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)

    assert await mod.agents.run("Foo") == "ok"
    sleep.assert_awaited_once_with(2.0)
    assert mod.agents.get_run.await_count == 2


def test_agents_polling_slows_as_run_ages():
    mod = _agents_module()
    assert mod._poll_interval(0) == 2.0
    assert mod._poll_interval(30) == 5.0
    assert mod._poll_interval(300) == 10.0


@pytest.mark.asyncio
async def test_agents_run_returns_run_id_when_wait_expires(monkeypatch):
    mod = _agents_module()
    monkeypatch.setattr(mod.agents, "enqueue", AsyncMock(return_value=MagicMock(run_id="run-1")))
    get_run = AsyncMock()
    monkeypatch.setattr(mod.agents, "get_run", get_run)

    pending = await mod.agents.run("Foo", timeout=0)

    assert pending.run_id == "run-1"
    assert pending.reason == "wait_timeout"
    assert pending.last_known_status is None
    get_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_agents_run_rejects_negative_timeout_before_enqueue(monkeypatch):
    mod = _agents_module()
    enqueue = AsyncMock()
    monkeypatch.setattr(mod.agents, "enqueue", enqueue)

    with pytest.raises(ValueError, match="non-negative"):
        await mod.agents.run("Foo", timeout=-1)

    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_agents_run_respects_workflow_deadline_margin(monkeypatch):
    mod = _agents_module()
    monkeypatch.setattr(mod.agents, "enqueue", AsyncMock(return_value=MagicMock(run_id="run-2")))
    get_run = AsyncMock()
    monkeypatch.setattr(mod.agents, "get_run", get_run)
    token = mod._execution_context.set(SimpleNamespace(
        workflow_deadline=datetime.now(timezone.utc) + timedelta(seconds=5),
        workflow_timeout_seconds=60,
    ))
    try:
        pending = await mod.agents.run("Foo")
    finally:
        mod._execution_context.reset(token)

    assert pending.run_id == "run-2"
    assert pending.reason == "workflow_deadline"
    get_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_sixty_second_workflow_can_wait_for_agent(monkeypatch):
    mod = _agents_module()
    monkeypatch.setattr(mod.agents, "enqueue", AsyncMock(return_value=MagicMock(run_id="run-4")))
    get_run = AsyncMock(return_value=MagicMock(status="completed", output={"text": "done"}))
    monkeypatch.setattr(mod.agents, "get_run", get_run)
    token = mod._execution_context.set(SimpleNamespace(
        workflow_deadline=datetime.now(timezone.utc) + timedelta(seconds=60),
        workflow_timeout_seconds=60,
    ))
    try:
        result = await mod.agents.run("Foo")
    finally:
        mod._execution_context.reset(token)

    assert result == "done"
    get_run.assert_awaited_once_with("run-4")
    assert mod._workflow_return_margin(60) == 9.0


@pytest.mark.asyncio
async def test_agents_wait_observes_cancelling_until_terminal(monkeypatch):
    mod = _agents_module()
    monkeypatch.setattr(mod.agents, "get_run", AsyncMock(side_effect=[
        MagicMock(status="cancelling"),
        MagicMock(status="cancelled", error="Cancelled by caller"),
    ]))
    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    with pytest.raises(RuntimeError, match="Cancelled by caller"):
        await mod.agents.wait("run-3")


@pytest.mark.asyncio
async def test_agents_wait_preserves_budget_exceeded_output(monkeypatch):
    mod = _agents_module()
    monkeypatch.setattr(mod.agents, "get_run", AsyncMock(return_value=MagicMock(
        status="budget_exceeded", output={"text": "Partial answer"},
    )))

    assert await mod.agents.wait("run-4") == "Partial answer"


@pytest.mark.asyncio
async def test_knowledge_store_many_forwards_default_timeout(monkeypatch):
    """``knowledge.store_many`` must default ``timeout=300`` to httpx."""
    mod = _knowledge_module()

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"ids": ["a", "b"]}
    mock_response.status_code = 200

    mock_client = MagicMock()
    mock_client.engine_request = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(mod, "get_client", lambda: mock_client)
    monkeypatch.setattr(mod, "resolve_scope", lambda s: s)

    await mod.knowledge.store_many(
        [{"content": "x"}, {"content": "y"}],
        namespace="faq",
    )

    kwargs = mock_client.engine_request.call_args.kwargs
    assert kwargs.get("timeout") == 300.0


@pytest.mark.asyncio
async def test_knowledge_store_many_respects_explicit_timeout(monkeypatch):
    """Explicit ``timeout=`` overrides the default."""
    mod = _knowledge_module()

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"ids": []}
    mock_response.status_code = 200

    mock_client = MagicMock()
    mock_client.engine_request = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(mod, "get_client", lambda: mock_client)
    monkeypatch.setattr(mod, "resolve_scope", lambda s: s)

    await mod.knowledge.store_many([], namespace="faq", timeout=600.0)

    kwargs = mock_client.engine_request.call_args.kwargs
    assert kwargs.get("timeout") == 600.0


@pytest.mark.asyncio
async def test_workflow_and_execution_facades_send_no_per_call_timeout(monkeypatch):
    """Migrated workflow/execution calls keep ``BifrostClient``'s default timeout.

    The fixed HTTP calls used the client's 30s default; ``engine_request``
    preserves that (both the socket and network clients carry the same 30s
    timeout), with no per-call override.
    """
    from bifrost.executions import executions
    from bifrost.workflows import workflows

    wf_meta = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "wf",
        "description": None,
        "category": None,
        "tags": [],
        "parameters": [],
        "execution_mode": "sync",
        "timeout_seconds": 1800,
        "retry_policy": None,
        "endpoint_enabled": False,
        "allowed_methods": None,
        "disable_global_key": False,
        "public_endpoint": False,
        "is_tool": False,
        "tool_description": None,
        "time_saved": None,
        "source_file_path": None,
        "relative_file_path": None,
    }
    summary = {
        "execution_id": "22222222-2222-2222-2222-222222222222",
        "workflow_name": "wf",
        "org_id": None,
        "form_id": None,
        "executed_by": "u",
        "executed_by_name": "U",
        "status": "Success",
        "result_type": None,
        "error_message": None,
        "duration_ms": None,
        "started_at": None,
        "completed_at": None,
        "session_id": None,
        "peak_memory_bytes": None,
        "process_rss_bytes": None,
        "cpu_total_seconds": None,
    }

    def _resp(body):
        resp = MagicMock()
        resp.status_code = 200
        resp.is_success = True
        resp.json = lambda: body
        resp.headers = {}
        return resp

    wf_client = MagicMock()
    wf_client.engine_request = AsyncMock(return_value=_resp([wf_meta]))
    monkeypatch.setattr(
        sys.modules["bifrost.workflows"], "get_client", lambda: wf_client
    )
    await workflows.list()
    assert "timeout" not in wf_client.engine_request.await_args.kwargs

    ex_client = MagicMock()
    ex_client.engine_request = AsyncMock(
        side_effect=[
            _resp({"executions": [summary], "continuation_token": None}),
            _resp(summary),
        ]
    )
    monkeypatch.setattr(
        sys.modules["bifrost.executions"], "get_client", lambda: ex_client
    )
    await executions.list()
    await executions.get(summary["execution_id"])
    for call in ex_client.engine_request.await_args_list:
        assert "timeout" not in call.kwargs

    mut_client = MagicMock()
    mut_client.engine_request = AsyncMock(
        side_effect=[
            _resp({"execution_id": "e1", "status": "Pending"}),
            _resp({"execution_id": "e1", "status": "Cancelled"}),
        ]
    )
    monkeypatch.setattr(
        sys.modules["bifrost.workflows"], "get_client", lambda: mut_client
    )
    await workflows.execute("wf")
    await workflows.cancel(summary["execution_id"])
    for call in mut_client.engine_request.await_args_list:
        assert "timeout" not in call.kwargs
