"""The forked agent child's entry: one dispatch, its own database pool."""
import os
from unittest.mock import AsyncMock, patch

import pytest

from src.jobs import agent_run_child


class _Consumer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[dict] = []
        self.bridge_urls: list[str | None] = []

    async def process_message(self, body: dict) -> None:
        self.messages.append(body)
        self.bridge_urls.append(os.environ.get("BIFROST_MCP_HTTP_BRIDGE_URL"))
        if self.error is not None:
            raise self.error


@pytest.fixture
def api_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("BIFROST_API_URL", "http://api:8000")
    # setenv first so teardown restores the variable's absence after the
    # code under test sets it.
    monkeypatch.setenv("BIFROST_MCP_HTTP_BRIDGE_URL", "")
    monkeypatch.delenv("BIFROST_MCP_HTTP_BRIDGE_URL")
    return "http://api:8000"


async def test_run_agent_message_initialises_runs_and_closes_the_database(api_url: str) -> None:
    consumer = _Consumer()
    message = {"run_id": "run-1"}
    calls: list[str] = []
    init_db = AsyncMock(side_effect=lambda: calls.append("init"))
    close_db = AsyncMock(side_effect=lambda: calls.append("close"))

    with (
        patch.object(agent_run_child, "AgentRunConsumer", return_value=consumer),
        patch.object(agent_run_child, "init_db", new=init_db),
        patch.object(agent_run_child, "close_db", new=close_db),
    ):
        await agent_run_child.run_agent_message(message)

    assert consumer.messages == [message]
    assert calls == ["init", "close"]


async def test_run_agent_message_points_the_rest_bridge_at_the_worker_api_url(api_url: str) -> None:
    consumer = _Consumer()

    with (
        patch.object(agent_run_child, "AgentRunConsumer", return_value=consumer),
        patch.object(agent_run_child, "init_db", new=AsyncMock()),
        patch.object(agent_run_child, "close_db", new=AsyncMock()),
    ):
        await agent_run_child.run_agent_message({"run_id": "run-1"})

    assert consumer.bridge_urls == [api_url]


async def test_run_agent_message_refuses_to_run_without_an_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BIFROST_API_URL", raising=False)
    # setenv first so teardown restores the variable's absence after the
    # code under test sets it.
    monkeypatch.setenv("BIFROST_MCP_HTTP_BRIDGE_URL", "")
    monkeypatch.delenv("BIFROST_MCP_HTTP_BRIDGE_URL")
    consumer = _Consumer()
    init_db = AsyncMock()

    with (
        patch.object(agent_run_child, "AgentRunConsumer", return_value=consumer),
        patch.object(agent_run_child, "init_db", new=init_db),
        patch.object(agent_run_child, "close_db", new=AsyncMock()),
        pytest.raises(RuntimeError, match="BIFROST_API_URL"),
    ):
        await agent_run_child.run_agent_message({"run_id": "run-1"})

    assert consumer.messages == []
    init_db.assert_not_awaited()
    assert "BIFROST_MCP_HTTP_BRIDGE_URL" not in os.environ


async def test_run_agent_message_closes_the_database_and_propagates_a_consumer_error(
    api_url: str,
) -> None:
    consumer = _Consumer(error=RuntimeError("model unavailable"))
    close_db = AsyncMock()

    with (
        patch.object(agent_run_child, "AgentRunConsumer", return_value=consumer),
        patch.object(agent_run_child, "init_db", new=AsyncMock()),
        patch.object(agent_run_child, "close_db", new=close_db),
        pytest.raises(RuntimeError, match="model unavailable"),
    ):
        await agent_run_child.run_agent_message({"run_id": "run-2"})

    close_db.assert_awaited_once_with()
