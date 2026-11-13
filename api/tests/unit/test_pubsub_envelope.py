"""Reader preparation preserves the bare wire contract across replica versions."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.pubsub import (
    ConnectionManager,
    _unwrap_envelope,
)


def test_reader_preserves_enclosed_payload():
    payload = {"type": "execution_update", "payload": "application data"}
    body, origin = _unwrap_envelope(
        {"v": 1, "origin": "publisher", "payload": payload}
    )
    assert body == payload
    assert origin == "publisher"


def test_bare_messages_pass_through_untouched():
    bare_cases = [
        {"type": "x"},
        {"payload": 1},
        {"v": 1},
        {"v": 1, "origin": "x"},
        {"v": 2, "origin": "x", "payload": 1},
        {"origin": "x", "payload": 1},
        None,
        "junk",
        [1, 2],
    ]
    for bare in bare_cases:
        body, origin = _unwrap_envelope(bare)
        assert body == bare
        assert origin is None


@pytest.mark.asyncio
async def test_broadcast_preserves_bare_wire_contract():
    manager = ConnectionManager()
    published = []

    def fake_get_redis():
        client = AsyncMock()

        async def capture(channel, body):
            published.append((channel, body))

        client.publish.side_effect = capture

        @asynccontextmanager
        async def context():
            yield client

        return context()

    payload = {"type": "agent_run_update", "run_id": "r1"}
    with patch("src.core.pubsub.get_redis", side_effect=fake_get_redis):
        await manager.broadcast("agent-runs", payload)

    assert len(published) == 1
    channel, body = published[0]
    assert channel == "bifrost:agent-runs"
    assert json.loads(body) == payload


@pytest.mark.asyncio
async def test_listener_delivers_every_origin_and_bare():
    """No origin filtering or local-first delivery is introduced."""
    manager = ConnectionManager()
    manager._send_local = AsyncMock()  # type: ignore[method-assign]
    captured = {}

    class FakeListener:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def start(self):
            return None

        async def stop(self):
            return None

    settings = SimpleNamespace(redis_url="redis://localhost:6379")
    with (
        patch("src.core.pubsub.ResilientPubSubListener", FakeListener),
        patch("src.core.pubsub.get_settings", return_value=settings),
    ):
        await manager._init_redis()
        try:
            on_message = captured["on_message"]
            self_payload = {"type": "n1"}
            foreign_payload = {"type": "n2"}
            bare_payload = {"type": "n3"}

            await on_message("bifrost:agent-runs", {"v": 1, "origin": "same-process", "payload": self_payload})
            await on_message(
                "bifrost:agent-runs",
                {"v": 1, "origin": "other-process", "payload": foreign_payload},
            )
            await on_message("bifrost:agent-runs", bare_payload)
        finally:
            await manager.close()

    sent = [c.args[1] for c in manager._send_local.await_args_list]
    assert sent == [self_payload, foreign_payload, bare_payload]


@pytest.mark.asyncio
@pytest.mark.parametrize("publisher_version", ["old", "new"])
@pytest.mark.parametrize("listener_version", ["old", "new"])
@pytest.mark.parametrize(
    ("channel", "payload", "delivered_channels"),
    [
        (
            "agent-runs",
            {"type": "agent_run_update", "run_id": "r1"},
            ["agent-runs"],
        ),
        (
            "service-logs:attempt",
            {"type": "service_log", "service_id": "service", "message": "ready"},
            ["service-logs:attempt", "service:service"],
        ),
        (
            "files:workspace:GLOBAL",
            {"type": "file_change", "path": "example.txt", "action": "write"},
            ["files:workspace:GLOBAL"],
        ),
    ],
)
async def test_mixed_versions_preserve_client_payload_and_authorized_delivery(
    publisher_version, listener_version, channel, payload, delivered_channels
):
    """Frozen eaa98d92 wire/callback contracts versus the actual new manager.

    All four directions cover direct upgrades from the pre-envelope version
    without an intermediate install, mixed replicas, and rollback. This is
    a transport contract test; Redis subscription/reconnect is covered elsewhere.
    No intermediate reader deployment is required while writers stay bare.
    """
    sender = ConnectionManager()
    receiver = ConnectionManager()
    delivered = []

    # Exercise the real local delivery authorization seam for each destination.
    for destination in delivered_channels:
        socket = AsyncMock()

        async def dispatch(received_channel, message):
            delivered.append((received_channel, message))

        socket._delivery_dispatcher = dispatch
        receiver.connections[destination] = {socket}

    async def old_receive(redis_channel, data):
        # Frozen pre-envelope callback from eaa98d92, pubsub.py:223-229.
        local_channel = redis_channel.replace("bifrost:", "")
        if local_channel == "queue:positions":
            receiver.request_queue_position_refresh()
            return
        await receiver._send_local(local_channel, data)
        await receiver._bridge_service_log(local_channel, data)

    captured = {}

    class FakeListener:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def start(self):
            return None

        async def stop(self):
            return None

    wire_messages = []

    async def publish(redis_channel, encoded):
        wire_messages.append(json.loads(encoded))
        callback = old_receive if listener_version == "old" else captured["on_message"]
        await callback(redis_channel, json.loads(encoded))

    redis = AsyncMock()
    redis.publish.side_effect = publish

    @asynccontextmanager
    async def redis_context():
        yield redis

    with (
        patch("src.core.pubsub.get_redis", redis_context),
        patch("src.core.pubsub.ResilientPubSubListener", FakeListener),
        patch("src.core.pubsub.get_settings", return_value=SimpleNamespace(redis_url="redis://unused")),
    ):
        if listener_version == "new":
            await receiver._init_redis()
        try:
            if publisher_version == "old":
                # Frozen eaa98d92 publish contract: bare JSON on bifrost:<channel>.
                await redis.publish(f"bifrost:{channel}", json.dumps(payload))
            else:
                await sender.broadcast(channel, payload)
        finally:
            await receiver.close()

    assert wire_messages == [payload]
    assert delivered == [(destination, payload) for destination in delivered_channels]
