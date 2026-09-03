import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.pubsub import (
    _PUBLISH_CHAT_RUN_EVENT_SCRIPT,
    publish_chat_run_event,
    replay_chat_run_events,
)


@pytest.mark.asyncio
async def test_chat_run_event_is_retained_before_realtime_broadcast():
    redis = AsyncMock()

    async def eval_script(_script, _num_keys, *_args):
        envelope = json.loads(_args[2])
        envelope["sequence"] = 7
        return json.dumps(envelope)

    redis.eval.side_effect = eval_script

    @asynccontextmanager
    async def redis_context():
        yield redis

    conversation_id = uuid4()
    run_id = uuid4()
    with (
        patch("src.core.pubsub.get_redis", return_value=redis_context()),
    ):
        event = await publish_chat_run_event(
            conversation_id,
            run_id,
            kind="run_status",
            status="queued",
            payload={"type": "run_status", "run_status": "queued"},
        )

    assert event["type"] == "chat_run_event"
    assert event["protocol_version"] == 1
    assert event["sequence"] == 7
    redis.eval.assert_awaited_once()
    eval_args = redis.eval.await_args.args
    assert eval_args[-1] == f"bifrost:chat:{conversation_id}"
    assert _PUBLISH_CHAT_RUN_EVENT_SCRIPT.index("'XADD'") < (
        _PUBLISH_CHAT_RUN_EVENT_SCRIPT.index("'PUBLISH'")
    )


@pytest.mark.asyncio
async def test_chat_run_event_replay_filters_by_conversation_sequence():
    redis = AsyncMock()
    redis.xrange.return_value = [
        (
            "1-0",
            {
                "event": json.dumps(
                    {
                        "type": "chat_run_event",
                        "protocol_version": 1,
                        "event_id": str(uuid4()),
                        "sequence": sequence,
                        "conversation_id": str(uuid4()),
                        "run_id": str(uuid4()),
                        "occurred_at": "2026-09-02T12:00:00+00:00",
                        "kind": "delta",
                        "status": "running",
                        "payload": {"type": "delta", "content": str(sequence)},
                    }
                )
            },
        )
        for sequence in (1, 2, 3)
    ]

    @asynccontextmanager
    async def redis_context():
        yield redis

    with patch("src.core.pubsub.get_redis", return_value=redis_context()):
        events = await replay_chat_run_events(uuid4(), after_sequence=1)

    assert [event["sequence"] for event in events] == [2, 3]
