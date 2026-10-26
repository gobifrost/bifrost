"""Regression tests for Redis pub/sub listener reconnect behavior.

Previously, ``_cancel_listener_loop`` and ``_command_listener_loop``
created their ``pubsub`` object once outside the outer ``while not
self._shutdown`` loop. When Redis closed the connection, the except
handler logged and slept, then re-entered ``get_message()`` on the same
dead pubsub — never reconnecting. This left affected worker pods unable
to receive cancel or recycle commands.

These tests assert that both loops construct a **new** pubsub after a
failure and continue delivering messages, without touching a real Redis.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as redis

from src.services.execution.process_pool import ProcessPoolManager
from tests.conftest import TEST_REDIS_URL


class _FakePubSub:
    """A fake pubsub that yields a scripted sequence of get_message results.

    Each entry in ``script`` is either a dict (delivered as a message) or
    an Exception instance (raised from get_message).
    """

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.subscribed_channels: list[str] = []
        self.unsubscribed_channels: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed_channels.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed_channels.append(channel)

    async def aclose(self) -> None:
        self.closed = True

    async def get_message(self, ignore_subscribe_messages: bool = False, timeout: float = 1.0):
        if not self._script:
            # Nothing left — block briefly, then return None so the loop can be stopped.
            await asyncio.sleep(0.01)
            return None
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeRedis:
    """A fake Redis client whose pubsub() returns successive _FakePubSub objects."""

    def __init__(self, pubsubs: list[_FakePubSub]) -> None:
        self._pubsubs = list(pubsubs)
        self.pubsub_call_count = 0

    def pubsub(self) -> _FakePubSub:
        self.pubsub_call_count += 1
        if self._pubsubs:
            return self._pubsubs.pop(0)
        # Fallback empty pubsub so the loop can continue shutting down.
        return _FakePubSub([])


def _make_pool() -> ProcessPoolManager:
    pool = ProcessPoolManager.__new__(ProcessPoolManager)
    pool.worker_id = "test-worker"
    pool._shutdown = False
    return pool


@pytest.mark.asyncio
async def test_cancel_listener_reconnects_after_connection_drop() -> None:
    """After get_message raises, the loop must create a fresh pubsub.

    Script: first pubsub raises ConnectionError → loop catches, sleeps,
    second pubsub delivers a real cancel message → loop handles it.
    """
    pool = _make_pool()

    dropped_pubsub = _FakePubSub([ConnectionError("Connection closed by server")])
    recovery_pubsub = _FakePubSub([
        {
            "type": "message",
            "data": '{"execution_id": "exec-123"}',
        },
    ])
    fake_redis = _FakeRedis([dropped_pubsub, recovery_pubsub])

    handled: list[str] = []

    async def fake_handle_cancel(execution_id: str) -> None:
        handled.append(execution_id)
        pool._shutdown = True  # stop the loop once we've proven reconnect worked

    pool._handle_cancel_request = fake_handle_cancel  # type: ignore[method-assign]

    with patch.object(pool, "_get_redis", new=AsyncMock(return_value=fake_redis)):
        # Shrink sleep so the test runs fast.
        with patch("src.services.execution.process_pool.asyncio.sleep", new=AsyncMock()):
            await asyncio.wait_for(pool._cancel_listener_loop(), timeout=2.0)

    assert fake_redis.pubsub_call_count == 2, (
        "Loop must create a new pubsub after a connection drop"
    )
    assert handled == ["exec-123"]
    # The dropped pubsub must be torn down on exit from the inner block.
    assert dropped_pubsub.closed is True


@pytest.mark.asyncio
async def test_command_listener_reconnects_after_connection_drop() -> None:
    """Same reconnect invariant for the command listener loop."""
    pool = _make_pool()

    dropped_pubsub = _FakePubSub([ConnectionError("Connection closed by server")])
    recovery_pubsub = _FakePubSub([
        {
            "type": "message",
            "data": '{"action": "recycle_all"}',
        },
    ])
    fake_redis = _FakeRedis([dropped_pubsub, recovery_pubsub])

    handled: list[dict] = []

    async def fake_handle_command(command: dict) -> None:
        handled.append(command)
        pool._shutdown = True

    pool._handle_command = fake_handle_command  # type: ignore[method-assign]

    with patch.object(pool, "_get_redis", new=AsyncMock(return_value=fake_redis)):
        with patch("src.services.execution.process_pool.asyncio.sleep", new=AsyncMock()):
            await asyncio.wait_for(pool._command_listener_loop(), timeout=2.0)

    assert fake_redis.pubsub_call_count == 2
    assert handled == [{"action": "recycle_all"}]
    assert dropped_pubsub.subscribed_channels == [
        f"bifrost:pool:{pool.worker_id}:commands"
    ]
    assert dropped_pubsub.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("listener", ["_cancel_listener_loop", "_command_listener_loop"])
async def test_listener_releases_real_pubsub_when_unsubscribe_fails(
    listener: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed unsubscribe must not retain real Redis PubSub connections."""
    client = redis.from_url(TEST_REDIS_URL, decode_responses=True)
    pubsubs = []
    try:
        for _ in range(3):
            pubsub = client.pubsub()
            pubsubs.append(pubsub)
            pool = _make_pool()

            async def dropped_unsubscribe(*_channels: str) -> None:
                raise redis.ConnectionError("connection dropped during unsubscribe")

            async def dropped_read(*_args, **_kwargs) -> None:
                pool._shutdown = True
                raise redis.ConnectionError("connection dropped while listening")

            monkeypatch.setattr(pubsub, "unsubscribe", dropped_unsubscribe)
            monkeypatch.setattr(pubsub, "get_message", dropped_read)
            monkeypatch.setattr(pool, "_get_redis", AsyncMock(return_value=client))
            monkeypatch.setattr(client, "pubsub", lambda: pubsub)

            with patch("src.services.execution.process_pool.asyncio.sleep", new=AsyncMock()):
                await getattr(pool, listener)()

            assert pubsub.connection is None
            assert not client.connection_pool._in_use_connections
    finally:
        for pubsub in pubsubs:
            await pubsub.aclose()
        await client.aclose()
