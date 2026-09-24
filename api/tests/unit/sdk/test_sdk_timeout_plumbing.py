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
    mock_client.post = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(mod, "get_client", lambda: mock_client)
    monkeypatch.setattr(mod, "resolve_scope", lambda s: s)

    await mod.knowledge.store_many(
        [{"content": "x"}, {"content": "y"}],
        namespace="faq",
    )

    kwargs = mock_client.post.call_args.kwargs
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
    mock_client.post = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(mod, "get_client", lambda: mock_client)
    monkeypatch.setattr(mod, "resolve_scope", lambda s: s)

    await mod.knowledge.store_many([], namespace="faq", timeout=600.0)

    kwargs = mock_client.post.call_args.kwargs
    assert kwargs.get("timeout") == 600.0
