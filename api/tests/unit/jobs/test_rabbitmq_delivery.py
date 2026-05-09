"""Unit tests for RabbitMQ delivery outcome decisions."""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.jobs.rabbitmq import (
    BaseConsumer,
    DuplicateMessage,
    PermanentConsumerError,
    RetryableConsumerError,
    _publish_once,
    infer_idempotency_key,
    rabbitmq,
)


class FakeMessage:
    def __init__(
        self,
        body: str | bytes,
        *,
        headers: dict[str, Any] | None = None,
        message_id: str = "msg-1",
    ):
        self.body = body if isinstance(body, bytes) else body.encode()
        self.headers = headers or {}
        self.message_id = message_id
        self.correlation_id = "corr-1"
        self.redelivered = False
        self.delivery_tag = 1
        self.routing_key = "unit-queue"
        self.exchange = ""
        self.ack = AsyncMock()
        self.nack = AsyncMock()
        self.reject = AsyncMock()

    def process(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("delivery outcomes should ack/nack/reject explicitly")


class UnitConsumer(BaseConsumer):
    def __init__(self, outcome: str = "success"):
        super().__init__(
            "unit-queue",
            prefetch_count=1,
            retry_delays_seconds=[1, 2],
            max_retry_attempts=2,
        )
        self.outcome = outcome
        self.retry_payloads: list[tuple[dict[str, Any], int, str]] = []
        self.poison_payloads: list[tuple[dict[str, Any], str]] = []

    async def process_message(self, body: dict[str, Any]) -> None:
        if self.outcome == "duplicate":
            raise DuplicateMessage("already handled")
        if self.outcome == "retry":
            raise RetryableConsumerError("temporarily broken")
        if self.outcome == "permanent":
            raise PermanentConsumerError("bad state")
        if self.outcome == "unexpected":
            raise RuntimeError("surprising failure")

    async def _publish_retry(self, message: Any, context: Any, *, reason: str) -> None:
        self.retry_payloads.append((context.body, context.retry_count + 1, reason))

    async def _publish_poison(self, message: Any, context: Any, *, reason: str) -> None:
        self.poison_payloads.append((context.body, reason))


class FakeExchange:
    def __init__(self) -> None:
        self.publish = AsyncMock()


class FakeChannel:
    def __init__(self) -> None:
        self.default_exchange = FakeExchange()
        self.poison_exchange = FakeExchange()
        self.declare_exchange = AsyncMock(return_value=self.poison_exchange)


@pytest.mark.asyncio
async def test_success_acks_message() -> None:
    consumer = UnitConsumer()
    message = FakeMessage(json.dumps({"ok": True}))

    await consumer._process_message_with_ack(message)

    message.ack.assert_awaited_once()
    message.nack.assert_not_awaited()
    message.reject.assert_not_awaited()
    assert consumer.retry_payloads == []
    assert consumer.poison_payloads == []


@pytest.mark.asyncio
async def test_duplicate_acks_message_without_retry() -> None:
    consumer = UnitConsumer("duplicate")
    message = FakeMessage(json.dumps({"ok": True}))

    await consumer._process_message_with_ack(message)

    message.ack.assert_awaited_once()
    message.nack.assert_not_awaited()
    assert consumer.retry_payloads == []
    assert consumer.poison_payloads == []


@pytest.mark.asyncio
async def test_retryable_error_publishes_retry_then_acks_original() -> None:
    consumer = UnitConsumer("retry")
    message = FakeMessage(json.dumps({"ok": True}))

    await consumer._process_message_with_ack(message)

    assert consumer.retry_payloads == [({"ok": True}, 1, "temporarily broken")]
    message.ack.assert_awaited_once()
    message.nack.assert_not_awaited()
    assert consumer.poison_payloads == []


@pytest.mark.asyncio
async def test_retry_publish_failure_requeues_original() -> None:
    consumer = UnitConsumer("retry")
    consumer._publish_retry = AsyncMock(side_effect=RuntimeError("broker down"))  # type: ignore[method-assign]
    message = FakeMessage(json.dumps({"ok": True}))

    await consumer._process_message_with_ack(message)

    message.ack.assert_not_awaited()
    message.nack.assert_awaited_once_with(requeue=True)


@pytest.mark.asyncio
async def test_max_retry_exhaustion_publishes_poison_then_acks_original() -> None:
    consumer = UnitConsumer("retry")
    message = FakeMessage(
        json.dumps({"ok": True}),
        headers={"x-retry-count": 2},
    )

    await consumer._process_message_with_ack(message)

    assert consumer.poison_payloads
    assert "retry attempts exhausted" in consumer.poison_payloads[0][1]
    message.ack.assert_awaited_once()
    message.nack.assert_not_awaited()


@pytest.mark.asyncio
async def test_permanent_error_publishes_poison_then_acks_original() -> None:
    consumer = UnitConsumer("permanent")
    message = FakeMessage(json.dumps({"ok": True}))

    await consumer._process_message_with_ack(message)

    assert consumer.poison_payloads == [({"ok": True}, "bad state")]
    message.ack.assert_awaited_once()
    message.nack.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_error_publishes_poison_then_acks_original() -> None:
    consumer = UnitConsumer("unexpected")
    message = FakeMessage(json.dumps({"ok": True}))

    await consumer._process_message_with_ack(message)

    assert consumer.poison_payloads == [({"ok": True}, "surprising failure")]
    message.ack.assert_awaited_once()
    message.nack.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_json_publishes_poison_then_acks_original() -> None:
    consumer = UnitConsumer()
    message = FakeMessage("{not-json")

    await consumer._process_message_with_ack(message)

    assert consumer.poison_payloads
    assert "malformed JSON" in consumer.poison_payloads[0][1]
    message.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_retry_routes_to_delayed_queue_with_retry_headers() -> None:
    consumer = BasePublishConsumer()
    channel = FakeChannel()
    consumer._channel = channel  # type: ignore[assignment]
    message = FakeMessage(json.dumps({"execution_id": "exec-1"}), message_id="msg-1")
    context = consumer._build_context(message)

    await consumer._publish_retry(message, context, reason="try later")

    channel.default_exchange.publish.assert_awaited_once()
    retry_call = channel.default_exchange.publish.await_args
    assert retry_call is not None
    published, = retry_call.args
    assert retry_call.kwargs == {"routing_key": "unit-queue-retry-1"}
    assert published.message_id == "msg-1"
    assert published.headers["x-retry-count"] == 1
    assert published.headers["x-replayed-count"] == 0
    assert published.headers["x-origin-queue"] == "unit-queue"
    assert published.headers["x-idempotency-key"] == "msg-1"
    assert published.headers["x-last-error"] == "try later"


@pytest.mark.asyncio
async def test_publish_poison_routes_to_dlx_with_reason_headers() -> None:
    consumer = BasePublishConsumer()
    channel = FakeChannel()
    consumer._channel = channel  # type: ignore[assignment]
    message = FakeMessage(json.dumps({"ok": True}), headers={"x-retry-count": 2})
    context = consumer._build_context(message)

    await consumer._publish_poison(message, context, reason="bad forever")

    channel.declare_exchange.assert_awaited_once()
    channel.poison_exchange.publish.assert_awaited_once()
    poison_call = channel.poison_exchange.publish.await_args
    assert poison_call is not None
    published, = poison_call.args
    assert poison_call.kwargs == {"routing_key": "unit-queue"}
    assert published.headers["x-retry-count"] == 2
    assert published.headers["x-poison-reason"] == "bad forever"
    assert "x-poisoned-at" in published.headers


class FakeDeclaredQueue:
    def __init__(self) -> None:
        self.bind = AsyncMock()


class FakePublishChannel:
    def __init__(self) -> None:
        self.default_exchange = FakeExchange()
        self.dead_letter_exchange = FakeExchange()
        self.declare_exchange = AsyncMock(return_value=self.dead_letter_exchange)
        self.declare_queue = AsyncMock(return_value=FakeDeclaredQueue())
        self.close = AsyncMock()


class FakeConnection:
    def __init__(self, channel: FakePublishChannel) -> None:
        self._channel = channel

    async def channel(self) -> FakePublishChannel:
        return self._channel


class FakeConnectionContext:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_publish_once_does_not_redeclare_retry_queues() -> None:
    channel = FakePublishChannel()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            rabbitmq,
            "get_connection",
            lambda: FakeConnectionContext(FakeConnection(channel)),
        )

        await _publish_once("unit-queue", {"id": "msg-1"}, 0)

    declared_names = [call.args[0] for call in channel.declare_queue.await_args_list]
    assert declared_names == ["unit-queue-poison", "unit-queue"]


@pytest.mark.asyncio
async def test_publish_once_bounds_message_id_and_preserves_full_idempotency_key() -> None:
    channel = FakePublishChannel()
    message = {"content": "x" * 600}
    full_idempotency_key = infer_idempotency_key("unit-queue", message)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            rabbitmq,
            "get_connection",
            lambda: FakeConnectionContext(FakeConnection(channel)),
        )

        await _publish_once("unit-queue", message, 0)

    publish_call = channel.default_exchange.publish.await_args
    assert publish_call is not None
    published, = publish_call.args
    assert len(published.message_id.encode()) <= 255
    assert published.message_id != full_idempotency_key
    assert published.headers["x-idempotency-key"] == full_idempotency_key
    assert published.headers["x-original-message-id"] == full_idempotency_key


class BasePublishConsumer(BaseConsumer):
    def __init__(self) -> None:
        super().__init__("unit-queue")

    async def process_message(self, body: dict[str, Any]) -> None:
        return None
