"""PR-1a: pub/sub envelope compatibility. No behavior change.

Covers the audit findings R-01/R-02 rollout: every publish stamps a
versioned origin envelope, and the listener delivers wrapped (self and
foreign) and bare messages alike. Suppression is PR-1b's job — a test
below pins that PR-1a code must NOT suppress self-origin.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from src.core.pubsub import (
    PROCESS_ORIGIN_ID,
    ConnectionManager,
    _unwrap_envelope,
    _wrap_envelope,
)


def test_origin_id_is_stable_process_uuid():
    UUID(PROCESS_ORIGIN_ID)  # raises if not a UUID
    assert _wrap_envelope({})["origin"] == PROCESS_ORIGIN_ID
    assert _wrap_envelope({})["origin"] == _wrap_envelope({})["origin"]


def test_envelope_round_trip_preserves_payload():
    payload = {
        "type": "execution_update",
        "executionId": "abc",
        "payload": "a bare 'payload' key must survive",
    }
    wrapped = _wrap_envelope(payload)
    assert wrapped["v"] == 1
    body, origin = _unwrap_envelope(wrapped)
    assert body == payload
    assert origin == PROCESS_ORIGIN_ID


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
async def test_broadcast_publishes_wrapped_envelope():
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
    unwrapped, origin = _unwrap_envelope(json.loads(body))
    assert unwrapped == payload
    assert origin == PROCESS_ORIGIN_ID


@pytest.mark.asyncio
async def test_listener_delivers_wrapped_self_foreign_and_bare():
    """PR-1a must not suppress: self-origin wrapped messages deliver."""
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

            await on_message("bifrost:agent-runs", _wrap_envelope(self_payload))
            await on_message(
                "bifrost:agent-runs",
                {**_wrap_envelope(foreign_payload), "origin": "other-process"},
            )
            await on_message("bifrost:agent-runs", bare_payload)
        finally:
            await manager.close()

    sent = [c.args[1] for c in manager._send_local.await_args_list]
    assert sent == [self_payload, foreign_payload, bare_payload]
