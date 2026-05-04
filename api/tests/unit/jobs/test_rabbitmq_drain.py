"""
Unit tests for graceful drain on RabbitMQ consumers.

Covers BaseConsumer.drain() and the related _on_message changes that track
in-flight tasks and reject new messages while draining. Tests are pure
unit tests — no real RabbitMQ connection. Channel/queue/message are mocked.
"""

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs.rabbitmq import BaseConsumer, BroadcastConsumer


class _NoopConsumer(BaseConsumer):
    """Test consumer with a configurable-delay process_message."""

    def __init__(self, queue_name: str = "test-queue", delay: float = 0.0):
        super().__init__(queue_name=queue_name)
        self.delay = delay
        self.processed: list[dict[str, Any]] = []

    async def process_message(self, body: dict[str, Any]) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.processed.append(body)


def _make_message_mock() -> MagicMock:
    """Build a mock IncomingMessage with async ack/nack methods."""
    message = MagicMock()
    message.body = b'{"hello": "world"}'
    message.message_id = "test-id"
    message.ack = AsyncMock()
    message.nack = AsyncMock()
    return message


class TestDrainWaitsForInflight:
    """drain() must wait for in-flight tasks to complete (not cancel them)."""

    @pytest.mark.asyncio
    async def test_waits_for_running_task(self):
        consumer = _NoopConsumer()
        # Build a real in-flight task that sleeps then completes.
        completed = asyncio.Event()

        async def slow_task():
            await asyncio.sleep(0.2)
            completed.set()

        task = asyncio.create_task(slow_task())
        consumer._inflight.add(task)
        task.add_done_callback(consumer._inflight.discard)

        # drain() with a generous deadline must wait for the task to finish.
        await consumer.drain(deadline=2.0)

        assert completed.is_set(), "drain returned before task completed"
        assert task.done()
        assert not task.cancelled(), "drain should not cancel the task"
        assert consumer._draining is True


class TestDrainRespectsDeadline:
    """drain() must give up after the deadline and log a warning."""

    @pytest.mark.asyncio
    async def test_returns_within_deadline(self, caplog):
        consumer = _NoopConsumer()

        async def too_slow():
            await asyncio.sleep(5.0)

        task = asyncio.create_task(too_slow())
        consumer._inflight.add(task)
        task.add_done_callback(consumer._inflight.discard)

        with caplog.at_level(logging.WARNING):
            start = asyncio.get_event_loop().time()
            await consumer.drain(deadline=0.1)
            elapsed = asyncio.get_event_loop().time() - start

        # Should return quickly (well under the 5s task duration).
        assert elapsed < 1.0, f"drain blocked too long: {elapsed}s"
        assert any("deadline exceeded" in r.message for r in caplog.records)

        # Cleanup so the test runner doesn't see a pending task. gather
        # with return_exceptions absorbs the expected CancelledError without
        # the awkward try/except-and-discard pattern.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class TestDrainIdempotent:
    """drain() called twice must no-op on the second call."""

    @pytest.mark.asyncio
    async def test_second_call_noops(self):
        consumer = _NoopConsumer()
        # First call: nothing to do, returns quickly.
        await consumer.drain(deadline=1.0)
        assert consumer._draining is True

        # Second call: must short-circuit. We assert this by ensuring
        # _queue.cancel is not invoked (we set it up after first drain).
        consumer._queue = MagicMock()
        consumer._queue.cancel = AsyncMock()
        consumer._consumer_tag = "tag-after-first-drain"

        await consumer.drain(deadline=1.0)

        consumer._queue.cancel.assert_not_called()


class TestOnMessageWhileDraining:
    """_on_message must nack messages that arrive after draining begins."""

    @pytest.mark.asyncio
    async def test_nacks_with_requeue(self):
        consumer = _NoopConsumer()
        consumer._draining = True
        message = _make_message_mock()

        await consumer._on_message(message)

        message.nack.assert_awaited_once_with(requeue=True)
        assert len(consumer._inflight) == 0


class TestOnMessageTracksTasks:
    """_on_message must add tasks to _inflight and discard them on completion."""

    @pytest.mark.asyncio
    async def test_task_added_and_removed(self):
        consumer = _NoopConsumer()
        consumer._process_message_with_ack = AsyncMock()  # type: ignore[method-assign]
        message = _make_message_mock()

        await consumer._on_message(message)

        # Right after creation: task is in the set.
        assert len(consumer._inflight) == 1
        task = next(iter(consumer._inflight))

        # Wait for the task to finish, then yield once so the done_callback
        # registered by _on_message runs and discards the task from _inflight.
        await asyncio.wait([task])
        await asyncio.sleep(0)

        assert len(consumer._inflight) == 0
        consumer._process_message_with_ack.assert_awaited_once_with(message)


class TestDrainCancelsConsumerTag:
    """drain() must call queue.cancel(consumer_tag) to stop new deliveries."""

    @pytest.mark.asyncio
    async def test_cancel_called_with_tag(self):
        consumer = _NoopConsumer()
        consumer._queue = MagicMock()
        consumer._queue.cancel = AsyncMock()
        consumer._consumer_tag = "test-tag"

        await consumer.drain(deadline=1.0)

        consumer._queue.cancel.assert_awaited_once_with("test-tag")


class TestDrainHandlesCancelException:
    """drain() must not propagate exceptions from queue.cancel — only log them."""

    @pytest.mark.asyncio
    async def test_cancel_exception_logged_not_raised(self, caplog):
        consumer = _NoopConsumer()
        consumer._queue = MagicMock()
        consumer._queue.cancel = AsyncMock(side_effect=Exception("boom"))
        consumer._consumer_tag = "test-tag"

        with caplog.at_level(logging.WARNING):
            # Must not raise.
            await consumer.drain(deadline=1.0)

        assert any("Error cancelling consumer" in r.message for r in caplog.records)


class TestBroadcastConsumerHasDrain:
    """BroadcastConsumer must have the same drain plumbing as BaseConsumer."""

    def test_broadcast_consumer_attributes(self):
        # Build a minimal subclass since BroadcastConsumer is abstract.
        class _NoopBroadcast(BroadcastConsumer):
            async def process_message(self, body: dict[str, Any]) -> None:
                pass

        consumer = _NoopBroadcast(exchange_name="test-fanout")

        # Confirm the new fields and method exist.
        assert hasattr(consumer, "_inflight")
        assert hasattr(consumer, "_consumer_tag")
        assert hasattr(consumer, "_draining")
        assert callable(getattr(consumer, "drain", None))
        assert isinstance(consumer._inflight, set)
        assert consumer._consumer_tag is None
        assert consumer._draining is False
