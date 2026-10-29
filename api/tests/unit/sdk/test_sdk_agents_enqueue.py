from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


def _agents_module():
    if "bifrost.agents" not in sys.modules:
        importlib.import_module("bifrost.agents")
    return sys.modules["bifrost.agents"]


@pytest.mark.asyncio
async def test_enqueue_returns_typed_handle_without_execution_wait(monkeypatch):
    mod = _agents_module()
    response = MagicMock(status_code=202, is_success=True)
    response.json.return_value = {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "status": "queued",
    }
    client = MagicMock()
    client.engine_request = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "get_client", lambda: client)

    handle = await mod.agents.enqueue(
        "Ticket Agent",
        input={"ticket_id": 42},
        output_schema={"type": "object"},
    )

    assert handle.run_id == "11111111-1111-1111-1111-111111111111"
    assert handle.status == "queued"
    client.engine_request.assert_awaited_once_with(
        "POST",
        "/api/agent-runs/enqueue",
        json={
            "agent_name": "Ticket Agent",
            "input": {"ticket_id": 42},
            "output_schema": {"type": "object"},
        },
    )


@pytest.mark.asyncio
async def test_enqueue_raises_agent_paused_error(monkeypatch):
    mod = _agents_module()
    response = MagicMock(status_code=200, is_success=True)
    response.json.return_value = {
        "status": "paused",
        "accepted": False,
        "message": "Agent is paused",
        "agent_id": "22222222-2222-2222-2222-222222222222",
    }
    client = MagicMock()
    client.engine_request = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "get_client", lambda: client)

    with pytest.raises(mod.AgentPausedError):
        await mod.agents.enqueue("Paused Agent")


@pytest.mark.asyncio
async def test_get_run_returns_typed_status(monkeypatch):
    mod = _agents_module()
    response = MagicMock(status_code=200, is_success=True)
    response.json.return_value = {
        "id": "11111111-1111-1111-1111-111111111111",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "agent_name": "Ticket Agent",
        "trigger_type": "api",
        "status": "queued",
        "created_at": "2026-08-24T12:00:00Z",
    }
    client = MagicMock()
    client.engine_request = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "get_client", lambda: client)

    run = await mod.agents.get_run("11111111-1111-1111-1111-111111111111")

    assert run.status == "queued"
    assert run.agent_name == "Ticket Agent"
    client.engine_request.assert_awaited_once_with(
        "GET", "/api/agent-runs/11111111-1111-1111-1111-111111111111"
    )


@pytest.mark.asyncio
async def test_get_run_translates_not_found(monkeypatch):
    mod = _agents_module()
    response = MagicMock(status_code=404, is_success=False)
    client = MagicMock()
    client.engine_request = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "get_client", lambda: client)

    with pytest.raises(ValueError, match="Agent run not found"):
        await mod.agents.get_run("missing")


_RUN_ID = "11111111-1111-1111-1111-111111111111"
_CONTOSO_USER = "33333333-3333-3333-3333-333333333333"


def _client_returning(mod, monkeypatch, body):
    response = MagicMock(status_code=202, is_success=True)
    response.json.return_value = body
    client = MagicMock()
    client.engine_request = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "get_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_enqueue_without_run_as_sends_no_run_as_key(monkeypatch):
    mod = _agents_module()
    client = _client_returning(
        mod, monkeypatch, {"run_id": _RUN_ID, "status": "queued", "run_as_user_id": None}
    )

    handle = await mod.agents.enqueue("Contoso Agent", input={"ticket_id": 7})

    assert handle.run_as_user_id is None
    client.engine_request.assert_awaited_once_with(
        "POST",
        "/api/agent-runs/enqueue",
        json={
            "agent_name": "Contoso Agent",
            "input": {"ticket_id": 7},
            "output_schema": None,
        },
    )


@pytest.mark.asyncio
async def test_enqueue_without_run_as_accepts_a_server_without_run_as(monkeypatch):
    mod = _agents_module()
    _client_returning(mod, monkeypatch, {"run_id": _RUN_ID, "status": "queued"})

    handle = await mod.agents.enqueue("Contoso Agent")

    assert handle.run_id == _RUN_ID
    assert handle.run_as_user_id is None


@pytest.mark.asyncio
async def test_enqueue_with_run_as_sends_it_and_returns_the_echo(monkeypatch):
    mod = _agents_module()
    client = _client_returning(
        mod,
        monkeypatch,
        {"run_id": _RUN_ID, "status": "queued", "run_as_user_id": _CONTOSO_USER},
    )

    handle = await mod.agents.enqueue(
        "Contoso Agent", input={"ticket_id": 7}, run_as=_CONTOSO_USER
    )

    assert handle.run_as_user_id == _CONTOSO_USER
    client.engine_request.assert_awaited_once_with(
        "POST",
        "/api/agent-runs/enqueue",
        json={
            "agent_name": "Contoso Agent",
            "input": {"ticket_id": 7},
            "output_schema": None,
            "run_as": _CONTOSO_USER,
        },
    )


@pytest.mark.asyncio
async def test_enqueue_with_run_as_accepts_a_null_echo(monkeypatch):
    """Naming yourself is a plain launch: the server answers with a null echo."""
    mod = _agents_module()
    _client_returning(
        mod, monkeypatch, {"run_id": _RUN_ID, "status": "queued", "run_as_user_id": None}
    )

    handle = await mod.agents.enqueue("Contoso Agent", run_as=_CONTOSO_USER)

    assert handle.run_as_user_id is None


@pytest.mark.asyncio
async def test_enqueue_with_run_as_refuses_a_server_without_run_as(monkeypatch):
    mod = _agents_module()
    _client_returning(mod, monkeypatch, {"run_id": _RUN_ID, "status": "queued"})

    with pytest.raises(
        RuntimeError, match="^This Bifrost server does not support Run As for agents$"
    ):
        await mod.agents.enqueue("Contoso Agent", run_as=_CONTOSO_USER)


@pytest.mark.asyncio
async def test_enqueue_with_run_as_on_a_paused_agent_raises_paused(monkeypatch):
    mod = _agents_module()
    _client_returning(
        mod,
        monkeypatch,
        {"status": "paused", "accepted": False, "message": "Agent is paused"},
    )

    with pytest.raises(mod.AgentPausedError):
        await mod.agents.enqueue("Paused Agent", run_as=_CONTOSO_USER)


@pytest.mark.asyncio
async def test_run_passes_run_as_to_enqueue(monkeypatch):
    mod = _agents_module()
    handle = mod.AgentRunHandle(run_id=_RUN_ID, run_as_user_id=_CONTOSO_USER)
    enqueue = AsyncMock(return_value=handle)
    wait = AsyncMock(return_value="done")
    monkeypatch.setattr(mod.agents, "enqueue", enqueue)
    monkeypatch.setattr(mod.agents, "wait", wait)

    result = await mod.agents.run(
        "Contoso Agent", {"ticket_id": 7}, timeout=5.0, run_as=_CONTOSO_USER
    )

    assert result == "done"
    enqueue.assert_awaited_once_with(
        "Contoso Agent", {"ticket_id": 7}, output_schema=None, run_as=_CONTOSO_USER
    )
    wait.assert_awaited_once_with(_RUN_ID, output_schema=None, timeout=5.0)


@pytest.mark.asyncio
async def test_run_without_run_as_passes_none(monkeypatch):
    mod = _agents_module()
    enqueue = AsyncMock(return_value=mod.AgentRunHandle(run_id=_RUN_ID))
    monkeypatch.setattr(mod.agents, "enqueue", enqueue)
    monkeypatch.setattr(mod.agents, "wait", AsyncMock(return_value="done"))

    await mod.agents.run("Contoso Agent")

    enqueue.assert_awaited_once_with(
        "Contoso Agent", None, output_schema=None, run_as=None
    )
