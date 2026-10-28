"""The forked agent child's entry: one dispatch, its own database pool."""
from unittest.mock import AsyncMock, patch

import pytest

from src.jobs import agent_run_child


class _Consumer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[dict] = []

    async def process_message(self, body: dict) -> None:
        self.messages.append(body)
        if self.error is not None:
            raise self.error


async def test_run_agent_message_initialises_runs_and_closes_the_database() -> None:
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


async def test_run_agent_message_closes_the_database_and_propagates_a_consumer_error() -> None:
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
