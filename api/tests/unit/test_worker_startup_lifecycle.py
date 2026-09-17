"""Worker startup/shutdown lifecycle races."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _BlockedConsumer:
    queue_name = "blocked-consumer"

    def __init__(
        self,
        started: asyncio.Event,
        release: asyncio.Event,
        drain_started: asyncio.Event,
        drain_release: asyncio.Event,
    ) -> None:
        self.started = started
        self.release = release
        self.drain_started = drain_started
        self.drain_release = drain_release
        self.start_calls = 0
        self.drain_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        self.started.set()
        await self.release.wait()

    async def drain(self, deadline: float = 300.0) -> None:
        self.drain_calls += 1
        self.drain_started.set()
        await self.drain_release.wait()


class _RecordingConsumer:
    queue_name = "recording-consumer"

    def __init__(self) -> None:
        self.start_calls = 0
        self.drain_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def drain(self, deadline: float = 300.0) -> None:
        self.drain_calls += 1


@pytest.mark.asyncio
async def test_stop_waits_for_blocked_startup_and_prevents_late_subscriptions():
    """SIGTERM during consumer startup must not let later consumers subscribe."""

    from src.worker.app import Worker

    blocked_started = asyncio.Event()
    release_blocked = asyncio.Event()
    drain_started = asyncio.Event()
    drain_release = asyncio.Event()
    blocked_consumer = _BlockedConsumer(
        blocked_started,
        release_blocked,
        drain_started,
        drain_release,
    )
    late_consumers = [_RecordingConsumer() for _ in range(5)]

    worker = Worker()
    worker.settings = MagicMock(environment="test")

    with (
        patch("src.worker.app.init_db", new=AsyncMock()),
        patch("src.worker.app.close_db", new=AsyncMock()),
        patch("src.worker.app.rabbitmq.close", new=AsyncMock()),
        patch(
            "src.services.agent_runtime.retry_transport.close_ai_retry_http_client",
            new=AsyncMock(),
        ),
        patch("src.worker.app.WorkflowExecutionConsumer", return_value=blocked_consumer),
        patch("src.worker.app.PackageInstallConsumer", return_value=late_consumers[0]),
        patch("src.worker.app.AgentRunConsumer", return_value=late_consumers[1]),
        patch("src.worker.app.SummarizeConsumer", return_value=late_consumers[2]),
        patch(
            "src.worker.app.SummarizeBackfillConsumer",
            return_value=late_consumers[3],
        ),
        patch("src.worker.app.TuneChatConsumer", return_value=late_consumers[4]),
    ):
        start_task = asyncio.create_task(worker.start())
        await blocked_started.wait()

        stop_task = asyncio.create_task(worker.stop())
        release_blocked.set()
        await drain_started.wait()
        assert not start_task.done()

        drain_release.set()
        await asyncio.wait_for(stop_task, timeout=2)
        await asyncio.wait_for(start_task, timeout=2)

    assert blocked_consumer.start_calls == 1
    assert blocked_consumer.drain_calls == 1
    assert [consumer.start_calls for consumer in late_consumers] == [0, 0, 0, 0, 0]
    assert [consumer.drain_calls for consumer in late_consumers] == [
        1,
        1,
        1,
        1,
        1,
    ]
    assert worker._shutdown_event.is_set()
