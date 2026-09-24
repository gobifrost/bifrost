"""SDK helper preserves paused and completed run behavior."""
from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


def _agents_module():
    """Resolve the ``bifrost.agents`` module bypassing the class shadowing in
    ``bifrost/__init__.py`` (``from .agents import agents`` rebinds the
    attribute to the class)."""
    if "bifrost.agents" not in sys.modules:
        importlib.import_module("bifrost.agents")
    return sys.modules["bifrost.agents"]


@pytest.mark.asyncio
async def test_run_raises_agent_paused_error_on_paused_response(monkeypatch):
    """When /enqueue returns ``status='paused'``, the SDK helper raises a typed
    exception so workflow code does not silently receive ``None``."""
    mod = _agents_module()
    paused_body = {
        "status": "paused",
        "accepted": False,
        "message": "Agent 'Foo' is paused. Request not processed.",
        "agent_id": "11111111-1111-1111-1111-111111111111",
    }

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = paused_body
    mock_response.status_code = 200

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    monkeypatch.setattr(mod, "get_client", lambda: mock_client)

    with pytest.raises(mod.AgentPausedError) as exc:
        await mod.agents.run("Foo", input={"x": 1})

    assert "paused" in str(exc.value).lower()
    assert exc.value.agent_id == "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_run_returns_output_for_normal_completion(monkeypatch):
    """A completed run still returns plain text to an unstructured caller."""
    mod = _agents_module()

    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"run_id": "run-1", "status": "queued"}
    mock_response.status_code = 200

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    monkeypatch.setattr(mod, "get_client", lambda: mock_client)
    monkeypatch.setattr(
        mod.agents,
        "get_run",
        AsyncMock(return_value=MagicMock(status="completed", output={"text": "hello"})),
    )

    result = await mod.agents.run("Foo", input={"x": 1})
    assert result == "hello"
