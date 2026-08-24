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
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "get_client", lambda: client)

    handle = await mod.agents.enqueue(
        "Ticket Agent",
        input={"ticket_id": 42},
        output_schema={"type": "object"},
    )

    assert handle.run_id == "11111111-1111-1111-1111-111111111111"
    assert handle.status == "queued"
    client.post.assert_awaited_once_with(
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
    client.post = AsyncMock(return_value=response)
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
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "get_client", lambda: client)

    run = await mod.agents.get_run("11111111-1111-1111-1111-111111111111")

    assert run.status == "queued"
    assert run.agent_name == "Ticket Agent"


@pytest.mark.asyncio
async def test_get_run_translates_not_found(monkeypatch):
    mod = _agents_module()
    response = MagicMock(status_code=404, is_success=False)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(mod, "get_client", lambda: client)

    with pytest.raises(ValueError, match="Agent run not found"):
        await mod.agents.get_run("missing")
