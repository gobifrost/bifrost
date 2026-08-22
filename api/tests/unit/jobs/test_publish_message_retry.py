"""
Unit tests for publish_message retry on transient broker errors.

Covers the readiness-flap scenario: when the rabbitmq Service endpoint
briefly drops (probe timeout), publish_message must retry on connection /
channel close errors before surfacing the failure to the caller. Real
errors (auth, malformed) propagate without retry.
"""

from unittest.mock import AsyncMock, patch

import aio_pika.exceptions
import pytest

from src.jobs import rabbitmq as rabbitmq_module
from src.jobs.rabbitmq import publish_message


@pytest.fixture(autouse=True)
def _zero_backoff(monkeypatch):
    """Skip real sleeps so tests run instantly."""
    monkeypatch.setattr(rabbitmq_module, "_PUBLISH_RETRY_DELAYS_S", (0.0, 0.0, 0.0))


@pytest.fixture(autouse=True)
def _stub_init_pools():
    """publish_message calls rabbitmq.init_pools(); stub it out."""
    with patch.object(rabbitmq_module.rabbitmq, "init_pools", AsyncMock(return_value=None)):
        yield


class TestPublishMessageRetry:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        with patch.object(
            rabbitmq_module, "_publish_once", AsyncMock(return_value=None)
        ) as mock_publish:
            await publish_message("q", {"k": "v"})
        assert mock_publish.await_count == 1

    @pytest.mark.asyncio
    async def test_recovers_from_connection_drop(self):
        attempts = AsyncMock(
            side_effect=[
                aio_pika.exceptions.AMQPConnectionError("boom"),
                None,
            ]
        )
        with patch.object(rabbitmq_module, "_publish_once", attempts):
            await publish_message("q", {"k": "v"})
        assert attempts.await_count == 2

    @pytest.mark.asyncio
    async def test_recovers_from_channel_close(self):
        attempts = AsyncMock(
            side_effect=[
                aio_pika.exceptions.ChannelClosed(0, "transient"),
                None,
            ]
        )
        with (
            patch.object(rabbitmq_module, "_publish_once", attempts),
            patch.object(
                rabbitmq_module.rabbitmq,
                "invalidate_publish_topology",
            ) as invalidate,
        ):
            await publish_message("q", {"k": "v"})
        assert attempts.await_count == 2
        invalidate.assert_called_once_with("q")

    @pytest.mark.asyncio
    async def test_exhausts_retries_then_raises(self):
        # 4 attempts total: initial + 3 retries; all fail.
        err = aio_pika.exceptions.ConnectionClosed(0, "still down")
        attempts = AsyncMock(side_effect=[err] * 4)
        with patch.object(rabbitmq_module, "_publish_once", attempts):
            with pytest.raises(aio_pika.exceptions.ConnectionClosed):
                await publish_message("q", {"k": "v"})
        assert attempts.await_count == 4

    @pytest.mark.asyncio
    async def test_does_not_retry_non_transient(self):
        err = aio_pika.exceptions.AuthenticationError("bad creds")
        attempts = AsyncMock(side_effect=err)
        with patch.object(rabbitmq_module, "_publish_once", attempts):
            with pytest.raises(aio_pika.exceptions.AuthenticationError):
                await publish_message("q", {"k": "v"})
        assert attempts.await_count == 1


@pytest.mark.asyncio
async def test_publish_topology_is_declared_once_per_queue():
    manager = rabbitmq_module.rabbitmq
    manager._publish_topology_ready.clear()
    manager._publish_topology_locks.clear()

    channel = AsyncMock()
    exchange = AsyncMock()
    poison_queue = AsyncMock()
    main_queue = AsyncMock()
    channel.declare_exchange.return_value = exchange
    channel.declare_queue.side_effect = [poison_queue, main_queue]

    await manager.ensure_publish_topology(channel, "workflow-executions")
    await manager.ensure_publish_topology(channel, "workflow-executions")

    channel.declare_exchange.assert_awaited_once()
    assert channel.declare_queue.await_count == 2
    poison_queue.bind.assert_awaited_once_with(
        exchange,
        routing_key="workflow-executions",
    )
