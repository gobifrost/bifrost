"""Tests for the DLQ operational CLI helpers."""

import pytest

from src.jobs.dlq_cli import decode_message, _describe, _fetch_poison_messages, _requeue_messages


class FakePoisonMessage:
    body = b'{"execution_id":"abc"}'
    message_id = "abc"
    correlation_id = "corr"
    headers = {
        "x-idempotency-key": "abc",
        "x-retry-count": 3,
        "x-replayed-count": 1,
        "x-origin-queue": "workflow-executions",
    }


def test_decode_message_handles_valid_json():
    assert decode_message(b'{"ok": true}') == {"ok": True}


def test_decode_message_handles_malformed_body():
    assert decode_message(b"{not-json") == "{not-json"


def test_describe_includes_operational_metadata():
    row = _describe("workflow-executions", FakePoisonMessage())

    assert row["poison_queue"] == "workflow-executions-poison"
    assert row["idempotency_key"] == "abc"
    assert row["retry_count"] == 3
    assert row["replay_count"] == 1
    assert row["body"] == {"execution_id": "abc"}


class FakePoisonQueue:
    def __init__(self, messages):
        self._messages = list(messages)

    async def get(self, *, fail: bool, no_ack: bool):
        del fail, no_ack
        if not self._messages:
            return None
        return self._messages.pop(0)


class FakeMessage:
    def __init__(self, message_id: str):
        self.message_id = message_id
        self.nacked = False

    async def nack(self, *, requeue: bool):
        assert requeue is True
        self.nacked = True


@pytest.mark.asyncio
async def test_fetch_poison_messages_stops_at_limit_and_empty_queue():
    queue = FakePoisonQueue([FakeMessage("one"), FakeMessage("two")])

    messages = await _fetch_poison_messages(queue, limit=3)

    assert [message.message_id for message in messages] == ["one", "two"]
    assert await _fetch_poison_messages(queue, limit=1) == []


@pytest.mark.asyncio
async def test_requeue_messages_nacks_each_message_once():
    messages = [FakeMessage("one"), FakeMessage("two")]

    await _requeue_messages(messages)

    assert all(message.nacked for message in messages)
