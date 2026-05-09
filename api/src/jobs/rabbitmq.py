"""
RabbitMQ Consumer Infrastructure

Provides the base consumer class and connection management for processing
background jobs from RabbitMQ queues.
"""

import asyncio
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aio_pika
from aio_pika import IncomingMessage
from aio_pika.abc import AbstractRobustConnection, AbstractRobustChannel
from aio_pika.pool import Pool

from src.config import get_settings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"
DEFAULT_RETRY_DELAYS_SECONDS = [10, 60, 300, 1800]
AMQP_SHORTSTR_MAX_BYTES = 255


class ConsumerDeliveryError(Exception):
    """Base class for explicit consumer delivery outcomes."""


class RetryableConsumerError(ConsumerDeliveryError):
    """Transient infrastructure or admission failure that should retry later."""


class PermanentConsumerError(ConsumerDeliveryError):
    """Message state that cannot succeed by trying again."""


class DuplicateMessage(ConsumerDeliveryError):
    """Message already has durable state and should be acknowledged."""


class MalformedMessage(PermanentConsumerError):
    """Message body or required metadata is malformed."""


class DomainFailureHandled(ConsumerDeliveryError):
    """Domain failure was recorded; broker message can be acknowledged."""


class ConsumerShutdown(RetryableConsumerError):
    """Consumer is stopping and the message should be retried safely."""


@dataclass(frozen=True)
class DeliveryContext:
    queue_name: str
    body: dict[str, Any]
    message_id: str | None
    correlation_id: str | None
    headers: dict[str, Any]
    redelivered: bool
    delivery_tag: int | None
    routing_key: str | None
    exchange: str | None
    retry_count: int
    replay_count: int
    idempotency_key: str | None
    enqueued_at: str | None


class RabbitMQConnection:
    """
    Manages RabbitMQ connection pool.

    Uses connection pooling for efficient resource usage across multiple consumers.
    """

    _instance: "RabbitMQConnection | None" = None
    _connection_pool: Pool | None = None
    _channel_pool: Pool | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_connection(self):
        """Get a connection context manager from the pool."""
        if self._connection_pool is None:
            raise RuntimeError("Connection pool not initialized. Call init_pools() first.")
        return self._connection_pool.acquire()

    def get_channel(self):
        """Get a channel context manager from the pool."""
        if self._channel_pool is None:
            raise RuntimeError("Channel pool not initialized. Call init_pools() first.")
        return self._channel_pool.acquire()

    async def init_pools(self) -> None:
        """Initialize connection and channel pools. Must be called before using the connection."""
        if self._connection_pool is not None:
            return  # Already initialized
        await self._init_pools()

    async def _init_pools(self) -> None:
        """Initialize connection and channel pools."""
        settings = get_settings()

        async def get_connection() -> AbstractRobustConnection:
            return await aio_pika.connect_robust(settings.rabbitmq_url)

        async def get_channel() -> AbstractRobustChannel:
            assert self._connection_pool is not None
            async with self._connection_pool.acquire() as connection:
                return await connection.channel()

        # Each consumer holds a connection, so pool size must be >= number of consumers
        # 5 consumers (workflow, package-install, agent-run, summarize, tune-chat) + 2 headroom
        self._connection_pool = Pool(get_connection, max_size=7)
        self._channel_pool = Pool(get_channel, max_size=10)

        logger.info("RabbitMQ connection pools initialized")

    async def close(self) -> None:
        """Close all connections."""
        if self._channel_pool:
            await self._channel_pool.close()
        if self._connection_pool:
            await self._connection_pool.close()
        logger.info("RabbitMQ connections closed")


# Global connection manager
rabbitmq = RabbitMQConnection()


class BaseConsumer(ABC):
    """
    Base class for RabbitMQ consumers.

    Provides:
    - Automatic connection and channel management
    - Message acknowledgment handling
    - Error handling with dead letter queue support
    - Graceful shutdown
    """

    def __init__(
        self,
        queue_name: str,
        prefetch_count: int = 1,
        dead_letter_exchange: str | None = None,
        retry_delays_seconds: list[int] | None = None,
        max_retry_attempts: int | None = None,
    ):
        """
        Initialize consumer.

        Args:
            queue_name: Name of the queue to consume from
            prefetch_count: Number of messages to prefetch (QoS)
            dead_letter_exchange: Exchange for failed messages (poison queue)
        """
        self.queue_name = queue_name
        self.prefetch_count = prefetch_count
        self.dead_letter_exchange = dead_letter_exchange or f"{queue_name}-dlx"
        self.retry_delays_seconds = retry_delays_seconds or DEFAULT_RETRY_DELAYS_SECONDS
        self.max_retry_attempts = max_retry_attempts or len(self.retry_delays_seconds)

        self._channel: AbstractRobustChannel | None = None
        self._queue: aio_pika.Queue | None = None
        self._running = False
        self._inflight: set[asyncio.Task] = set()
        self._consumer_tag: str | None = None
        self._draining: bool = False

    async def start(self) -> None:
        """Start consuming messages."""
        self._running = True

        # Initialize pools and get a dedicated connection for this consumer
        await rabbitmq.init_pools()
        # Store the context manager so it stays open
        self._connection_ctx = rabbitmq.get_connection()
        connection = await self._connection_ctx.__aenter__()
        channel = await connection.channel()
        self._channel = channel
        await channel.set_qos(prefetch_count=self.prefetch_count)

        # Declare dead letter exchange
        dlx = await channel.declare_exchange(
            self.dead_letter_exchange,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        # Declare dead letter queue
        dlq = await channel.declare_queue(
            f"{self.queue_name}-poison",
            durable=True,
        )
        await dlq.bind(dlx, routing_key=self.queue_name)

        for idx, delay in enumerate(self.retry_delays_seconds, start=1):
            await channel.declare_queue(
                f"{self.queue_name}-retry-{idx}",
                durable=True,
                arguments={
                    "x-message-ttl": delay * 1000,
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": self.queue_name,
                },
            )

        # Declare main queue with dead letter routing
        queue = await channel.declare_queue(
            self.queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": self.dead_letter_exchange,
                "x-dead-letter-routing-key": self.queue_name,
            },
        )
        self._queue = queue

        logger.info(f"Consumer started for queue: {self.queue_name}")

        # Start consuming, capturing the consumer tag so drain() can cancel it.
        self._consumer_tag = await queue.consume(self._on_message)

    async def stop(self) -> None:
        """Stop consuming messages (hard close — does NOT wait for in-flight).

        For graceful shutdown, call drain() instead.
        """
        self._running = False
        if self._channel:
            await self._channel.close()
        if hasattr(self, '_connection_ctx') and self._connection_ctx:
            await self._connection_ctx.__aexit__(None, None, None)
        logger.info(f"Consumer stopped for queue: {self.queue_name}")

    async def drain(self, deadline: float = 300.0) -> None:
        """Stop new deliveries, wait on in-flight tasks, then close.

        Cancels the consumer tag (RabbitMQ stops sending new messages on this
        channel) but keeps the channel open so in-flight tasks can ack their
        work. After the deadline expires (or all tasks finish), calls stop()
        to close the channel + connection.

        Idempotent — calling twice is a no-op on the second call.

        Args:
            deadline: Max seconds to wait for in-flight tasks before giving up.
        """
        if self._draining:
            return  # idempotent
        self._draining = True

        try:
            # Cancel the consumer: stops new deliveries, keeps channel open.
            if self._queue is not None and self._consumer_tag is not None:
                try:
                    await self._queue.cancel(self._consumer_tag)
                    logger.info(f"Cancelled consumer for {self.queue_name}")
                except Exception as e:
                    logger.warning(f"Error cancelling consumer for {self.queue_name}: {e}")

            # Snapshot is intentional: any message that races past the _draining
            # flag gets nacked + requeued in _on_message and never enters _inflight.
            if self._inflight:
                pending = list(self._inflight)
                logger.info(
                    f"Draining {len(pending)} in-flight on {self.queue_name} "
                    f"(deadline={deadline}s)"
                )
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=deadline,
                    )
                    logger.info(f"Drain complete for {self.queue_name}")
                except asyncio.TimeoutError:
                    still_running = [t for t in pending if not t.done()]
                    logger.warning(
                        f"Drain deadline exceeded on {self.queue_name}: "
                        f"{len(still_running)} task(s) still running"
                    )
        finally:
            # Always close channel + connection, even if cancelled mid-drain.
            await self.stop()

    async def _on_message(self, message: IncomingMessage) -> None:
        """
        Handle incoming message.

        Spawns a task to process each message concurrently, allowing
        multiple messages to be processed in parallel up to prefetch_count.
        Tracks in-flight tasks so drain() can wait for them.
        """
        if self._draining:
            # Consumer was cancelled but a message slipped through; nack to
            # requeue so another worker picks it up.
            await message.nack(requeue=True)
            return
        task = asyncio.create_task(self._process_message_with_ack(message))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _process_message_with_ack(self, message: IncomingMessage) -> None:
        """
        Process a message with proper acknowledgment handling.

        This runs as a separate task to enable concurrent message processing.
        """
        started = asyncio.get_running_loop().time()
        context: DeliveryContext | None = None
        try:
            context = self._build_context(message)

            logger.info(
                f"Processing message from {self.queue_name}",
                extra=self._log_extra(context),
            )

            await self.process_message(context.body)
            await message.ack()
            self._log_decision("ack", context, duration=self._duration(started))
        except DuplicateMessage as e:
            await message.ack()
            self._log_decision("duplicate_ack", context, reason=str(e), duration=self._duration(started))
        except DomainFailureHandled as e:
            await message.ack()
            self._log_decision("domain_failure_ack", context, reason=str(e), duration=self._duration(started))
        except RetryableConsumerError as e:
            await self._retry_or_poison(message, context, reason=str(e), started=started)
        except PermanentConsumerError as e:
            await self._dead_letter_and_ack(message, context, reason=str(e), started=started)
        except json.JSONDecodeError as e:
            malformed = self._malformed_context(message)
            await self._dead_letter_and_ack(message, malformed, reason=f"malformed JSON: {e}", started=started)
        except asyncio.CancelledError:
            if context is None:
                await message.nack(requeue=True)
            else:
                await self._retry_or_poison(message, context, reason="consumer shutdown", started=started)
            raise
        except Exception as e:
            logger.exception(
                "Unhandled consumer exception; dead-lettering message",
                extra=self._log_extra(context, reason=str(e), error_type=type(e).__name__),
            )
            await self._dead_letter_and_ack(message, context, reason=str(e), started=started)

    def _build_context(self, message: IncomingMessage) -> DeliveryContext:
        body = json.loads(message.body.decode())
        if not isinstance(body, dict):
            raise MalformedMessage("message body must be a JSON object")
        headers = dict(message.headers or {})
        retry_count = int(headers.get("x-retry-count") or 0)
        replay_count = int(headers.get("x-replayed-count") or 0)
        return DeliveryContext(
            queue_name=self.queue_name,
            body=body,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            headers=headers,
            redelivered=bool(message.redelivered),
            delivery_tag=getattr(message, "delivery_tag", None),
            routing_key=getattr(message, "routing_key", None),
            exchange=getattr(message, "exchange", None),
            retry_count=retry_count,
            replay_count=replay_count,
            idempotency_key=headers.get("x-idempotency-key") or message.message_id,
            enqueued_at=headers.get("x-enqueued-at"),
        )

    def _malformed_context(self, message: IncomingMessage) -> DeliveryContext:
        headers = dict(message.headers or {})
        return DeliveryContext(
            queue_name=self.queue_name,
            body={"_malformed_body": message.body.decode(errors="replace")},
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            headers=headers,
            redelivered=bool(message.redelivered),
            delivery_tag=getattr(message, "delivery_tag", None),
            routing_key=getattr(message, "routing_key", None),
            exchange=getattr(message, "exchange", None),
            retry_count=int(headers.get("x-retry-count") or 0),
            replay_count=int(headers.get("x-replayed-count") or 0),
            idempotency_key=headers.get("x-idempotency-key") or message.message_id,
            enqueued_at=headers.get("x-enqueued-at"),
        )

    async def _retry_or_poison(
        self,
        message: IncomingMessage,
        context: DeliveryContext | None,
        *,
        reason: str,
        started: float,
    ) -> None:
        if context is None:
            await message.nack(requeue=True)
            return
        if context.retry_count >= self.max_retry_attempts:
            await self._dead_letter_and_ack(
                message,
                context,
                reason=f"retry attempts exhausted: {reason}",
                started=started,
            )
            return
        try:
            await self._publish_retry(message, context, reason=reason)
        except Exception:
            logger.exception(
                "Failed to publish delayed retry; requeueing original message",
                extra=self._log_extra(context, reason=reason),
            )
            await message.nack(requeue=True)
            return
        await message.ack()
        self._log_decision("retry_scheduled", context, reason=reason, duration=self._duration(started))

    async def _dead_letter_and_ack(
        self,
        message: IncomingMessage,
        context: DeliveryContext | None,
        *,
        reason: str,
        started: float,
    ) -> None:
        if context is None:
            await message.reject(requeue=False)
            return
        try:
            await self._publish_poison(message, context, reason=reason)
        except Exception:
            logger.exception(
                "Failed to publish poison message; requeueing original",
                extra=self._log_extra(context, reason=reason),
            )
            await message.nack(requeue=True)
            return
        await message.ack()
        self._log_decision("dead_lettered", context, reason=reason, duration=self._duration(started))

    async def _publish_retry(self, message: IncomingMessage, context: DeliveryContext, *, reason: str) -> None:
        if self._channel is None:
            raise RuntimeError("consumer channel is not available")
        next_retry = context.retry_count + 1
        retry_queue = f"{self.queue_name}-retry-{min(next_retry, len(self.retry_delays_seconds))}"
        context_headers = dict(context.headers)
        if context.idempotency_key:
            context_headers.setdefault("x-idempotency-key", context.idempotency_key)
        headers = _message_headers(
            context.body,
            self.queue_name,
            message_id=context.message_id,
            headers=context_headers,
            retry_count=next_retry,
            replay_count=context.replay_count,
        )
        headers["x-last-error"] = reason[:500]
        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(context.body).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=context.message_id,
                correlation_id=context.correlation_id,
                headers=headers,
            ),
            routing_key=retry_queue,
        )

    async def _publish_poison(self, message: IncomingMessage, context: DeliveryContext, *, reason: str) -> None:
        if self._channel is None:
            raise RuntimeError("consumer channel is not available")
        context_headers = dict(context.headers)
        if context.idempotency_key:
            context_headers.setdefault("x-idempotency-key", context.idempotency_key)
        headers = _message_headers(
            context.body,
            self.queue_name,
            message_id=context.message_id,
            headers=context_headers,
            retry_count=context.retry_count,
            replay_count=context.replay_count,
        )
        headers["x-poison-reason"] = reason[:500]
        headers["x-poisoned-at"] = datetime.now(timezone.utc).isoformat()
        exchange = await self._channel.declare_exchange(
            self.dead_letter_exchange,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(context.body).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=context.message_id,
                correlation_id=context.correlation_id,
                headers=headers,
            ),
            routing_key=self.queue_name,
        )

    def _duration(self, started: float) -> float:
        return asyncio.get_running_loop().time() - started

    def _log_extra(
        self,
        context: DeliveryContext | None,
        *,
        reason: str | None = None,
        error_type: str | None = None,
        duration: float | None = None,
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {"queue": self.queue_name}
        if reason is not None:
            extra["reason"] = reason
        if error_type is not None:
            extra["error_type"] = error_type
        if duration is not None:
            extra["duration_seconds"] = duration
        if context is not None:
            extra.update(
                {
                    "message_id": context.message_id,
                    "correlation_id": context.correlation_id,
                    "idempotency_key": context.idempotency_key,
                    "retry_count": context.retry_count,
                    "replay_count": context.replay_count,
                    "redelivered": context.redelivered,
                }
            )
            if context.enqueued_at:
                extra["enqueued_at"] = context.enqueued_at
        return extra

    def _log_decision(
        self,
        decision: str,
        context: DeliveryContext | None,
        *,
        reason: str | None = None,
        duration: float | None = None,
    ) -> None:
        logger.info(
            "RabbitMQ message decision",
            extra={"decision": decision, **self._log_extra(context, reason=reason, duration=duration)},
        )

    @abstractmethod
    async def process_message(self, body: dict[str, Any]) -> None:
        """
        Process a message from the queue.

        Must be implemented by subclasses.

        Args:
            body: Parsed message body
        """
        pass


def infer_idempotency_key(queue_name: str, message: dict[str, Any]) -> str:
    """Return a stable key used in broker headers for observability and replay."""
    if "idempotency_key" in message:
        return str(message["idempotency_key"])
    if queue_name == "workflow-executions" and message.get("execution_id"):
        return str(message["execution_id"])
    if queue_name == "agent-runs" and message.get("run_id"):
        return str(message["run_id"])
    if queue_name == "agent-summarization" and message.get("run_id"):
        return str(message["run_id"])
    if queue_name == "agent-summarization-backfill" and message.get("run_id"):
        return f"{message['run_id']}:{message.get('backfill_job_id') or 'live'}"
    if queue_name == "agent-tuning-chat" and message.get("turn_id"):
        return str(message["turn_id"])
    if queue_name == "agent-tuning-chat" and message.get("run_id"):
        return f"{message['run_id']}:{message.get('message_id') or message.get('content')}"
    return str(message.get("id") or json.dumps(message, sort_keys=True, default=str))


def _bounded_message_id(message_id: str) -> str:
    """Return a value safe for AMQP shortstr message_id properties."""
    if len(message_id.encode("utf-8")) <= AMQP_SHORTSTR_MAX_BYTES:
        return message_id
    return f"sha256:{hashlib.sha256(message_id.encode('utf-8')).hexdigest()}"


def _message_headers(
    message: dict[str, Any],
    origin_queue: str,
    *,
    message_id: str | None = None,
    headers: dict[str, Any] | None = None,
    retry_count: int = 0,
    replay_count: int = 0,
) -> dict[str, Any]:
    merged = dict(headers or {})
    idempotency_key = merged.get("x-idempotency-key") or infer_idempotency_key(origin_queue, message)
    merged.update(
        {
            "x-idempotency-key": str(idempotency_key),
            "x-origin-queue": merged.get("x-origin-queue") or origin_queue,
            "x-schema-version": merged.get("x-schema-version") or SCHEMA_VERSION,
            "x-enqueued-at": merged.get("x-enqueued-at") or datetime.now(timezone.utc).isoformat(),
            "x-retry-count": retry_count,
            "x-replayed-count": replay_count,
        }
    )
    if message_id and "x-original-message-id" not in merged:
        merged["x-original-message-id"] = message_id
    return merged


class BroadcastConsumer(ABC):
    """
    Base class for broadcast (fanout) consumers.

    Unlike BaseConsumer where one worker gets each message,
    BroadcastConsumer delivers each message to ALL workers.
    Each worker creates an exclusive, auto-delete queue bound to a fanout exchange.

    Use this for operations that need to run on every worker instance,
    such as package installation or cache invalidation.
    """

    def __init__(self, exchange_name: str):
        """
        Initialize broadcast consumer.

        Args:
            exchange_name: Name of the fanout exchange to consume from
        """
        self.exchange_name = exchange_name
        self._channel: AbstractRobustChannel | None = None
        self._queue: aio_pika.Queue | None = None
        self._running = False
        self._connection_ctx = None
        self._inflight: set[asyncio.Task] = set()
        self._consumer_tag: str | None = None
        self._draining: bool = False

    @property
    def queue_name(self) -> str:
        """Return the exchange name for logging compatibility."""
        return f"{self.exchange_name} (broadcast)"

    async def start(self) -> None:
        """Start consuming messages from the fanout exchange."""
        self._running = True

        # Initialize pools and get a dedicated connection for this consumer
        await rabbitmq.init_pools()
        self._connection_ctx = rabbitmq.get_connection()
        connection = await self._connection_ctx.__aenter__()
        channel = await connection.channel()
        self._channel = channel
        await channel.set_qos(prefetch_count=1)

        # Declare fanout exchange
        exchange = await channel.declare_exchange(
            self.exchange_name,
            aio_pika.ExchangeType.FANOUT,
            durable=True,
        )

        # Create exclusive, auto-delete queue (unique per worker)
        # Empty name = RabbitMQ generates unique name
        # exclusive = only this consumer can use it
        # auto_delete = delete when consumer disconnects
        queue = await channel.declare_queue(
            "",
            exclusive=True,
            auto_delete=True,
        )
        self._queue = queue

        # Bind queue to fanout exchange
        await queue.bind(exchange)

        logger.info(f"Broadcast consumer started for exchange: {self.exchange_name}")

        # Start consuming, capturing the consumer tag so drain() can cancel it.
        self._consumer_tag = await queue.consume(self._on_message)

    async def stop(self) -> None:
        """Stop consuming messages (hard close — does NOT wait for in-flight).

        For graceful shutdown, call drain() instead.
        """
        self._running = False
        if self._channel:
            await self._channel.close()
        if self._connection_ctx:
            await self._connection_ctx.__aexit__(None, None, None)
        logger.info(f"Broadcast consumer stopped for exchange: {self.exchange_name}")

    async def drain(self, deadline: float = 300.0) -> None:
        """Stop new deliveries, wait on in-flight tasks, then close.

        Cancels the consumer tag (RabbitMQ stops sending new messages on this
        channel) but keeps the channel open so in-flight tasks can ack their
        work. After the deadline expires (or all tasks finish), calls stop()
        to close the channel + connection.

        Idempotent — calling twice is a no-op on the second call.

        Args:
            deadline: Max seconds to wait for in-flight tasks before giving up.
        """
        if self._draining:
            return  # idempotent
        self._draining = True

        try:
            # Cancel the consumer: stops new deliveries, keeps channel open.
            if self._queue is not None and self._consumer_tag is not None:
                try:
                    await self._queue.cancel(self._consumer_tag)
                    logger.info(f"Cancelled consumer for {self.queue_name}")
                except Exception as e:
                    logger.warning(f"Error cancelling consumer for {self.queue_name}: {e}")

            # Snapshot is intentional: any message that races past the _draining
            # flag gets nacked + requeued in _on_message and never enters _inflight.
            if self._inflight:
                pending = list(self._inflight)
                logger.info(
                    f"Draining {len(pending)} in-flight on {self.queue_name} "
                    f"(deadline={deadline}s)"
                )
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=deadline,
                    )
                    logger.info(f"Drain complete for {self.queue_name}")
                except asyncio.TimeoutError:
                    still_running = [t for t in pending if not t.done()]
                    logger.warning(
                        f"Drain deadline exceeded on {self.queue_name}: "
                        f"{len(still_running)} task(s) still running"
                    )
        finally:
            # Always close channel + connection, even if cancelled mid-drain.
            await self.stop()

    async def _on_message(self, message: IncomingMessage) -> None:
        """Handle incoming message.

        Tracks in-flight tasks so drain() can wait for them. While draining,
        slipped-through messages are nacked + requeued so another worker
        picks them up.
        """
        if self._draining:
            await message.nack(requeue=True)
            return
        task = asyncio.create_task(self._process_message_with_ack(message))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _process_message_with_ack(self, message: IncomingMessage) -> None:
        """Process a message with proper acknowledgment handling."""
        async with message.process(requeue=False):
            try:
                body = json.loads(message.body.decode())

                logger.info(
                    f"Processing broadcast message from {self.exchange_name}",
                    extra={"message_id": message.message_id},
                )

                await self.process_message(body)

                logger.info(
                    "Broadcast message processed successfully",
                    extra={"message_id": message.message_id},
                )

            except Exception as e:
                logger.error(
                    f"Error processing broadcast message from {self.exchange_name}: {e}",
                    extra={
                        "message_id": message.message_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                # For broadcast, we don't have DLQ since each worker has its own queue
                # Just log the error and continue
                raise

    @abstractmethod
    async def process_message(self, body: dict[str, Any]) -> None:
        """
        Process a broadcast message.

        Must be implemented by subclasses.

        Args:
            body: Parsed message body
        """
        pass


async def publish_broadcast(
    exchange_name: str,
    message: dict[str, Any],
) -> None:
    """
    Publish a message to a fanout exchange (broadcast to all consumers).

    Args:
        exchange_name: Target fanout exchange name
        message: Message body (will be JSON encoded)
    """
    await rabbitmq.init_pools()
    async with rabbitmq.get_connection() as connection:
        channel = await connection.channel()

        try:
            # Declare fanout exchange
            exchange = await channel.declare_exchange(
                exchange_name,
                aio_pika.ExchangeType.FANOUT,
                durable=True,
            )

            # Publish to exchange (fanout ignores routing key)
            await exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key="",
            )

            logger.debug(f"Published broadcast message to {exchange_name}")

        finally:
            await channel.close()


async def publish_to_exchange(
    exchange_name: str,
    message: dict[str, Any],
    routing_key: str = "",
) -> None:
    """
    Publish a message to a specific exchange.

    Used for streaming responses where consumers create temporary queues.
    Unlike publish_broadcast which uses fanout, this can use any exchange type.

    Args:
        exchange_name: Target exchange name
        message: Message body (will be JSON encoded)
        routing_key: Optional routing key for topic/direct exchanges
    """
    await rabbitmq.init_pools()
    async with rabbitmq.get_connection() as connection:
        channel = await connection.channel()

        try:
            # Declare exchange (fanout for simple broadcast)
            exchange = await channel.declare_exchange(
                exchange_name,
                aio_pika.ExchangeType.FANOUT,
                durable=False,  # Transient for streaming
                auto_delete=True,  # Delete when no bindings
            )

            # Publish message
            await exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode(),
                    delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,  # Fast, no disk
                ),
                routing_key=routing_key,
            )

            logger.debug(f"Published streaming message to exchange {exchange_name}")

        finally:
            await channel.close()


async def consume_from_exchange(
    exchange_name: str,
    timeout: float | None = None,
):
    """
    Consume messages from an exchange using a temporary queue.

    Creates an exclusive, auto-delete queue bound to the exchange.
    Yields messages as they arrive. Queue is deleted when iteration stops.

    Args:
        exchange_name: Exchange to consume from
        timeout: Optional timeout in seconds to wait for messages

    Yields:
        dict: Parsed message bodies
    """
    await rabbitmq.init_pools()
    connection_ctx = rabbitmq.get_connection()
    connection = await connection_ctx.__aenter__()

    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        # Declare exchange (must match publisher)
        exchange = await channel.declare_exchange(
            exchange_name,
            aio_pika.ExchangeType.FANOUT,
            durable=False,
            auto_delete=True,
        )

        # Create exclusive, auto-delete queue for this consumer
        queue = await channel.declare_queue(
            "",  # RabbitMQ generates unique name
            exclusive=True,
            auto_delete=True,
        )

        # Bind to exchange
        await queue.bind(exchange)

        logger.debug(f"Consuming from exchange {exchange_name} via queue {queue.name}")

        # Use async iterator with optional timeout
        async with queue.iterator(timeout=timeout) as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    try:
                        body = json.loads(message.body.decode())
                        yield body

                        # Check for done signal to stop iteration
                        if body.get("type") == "done" or body.get("type") == "error":
                            logger.debug("Received terminal message, stopping consumer")
                            break

                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON in message: {e}")
                        continue

    except asyncio.TimeoutError:
        logger.debug(f"Timeout consuming from exchange {exchange_name}")
    finally:
        await connection_ctx.__aexit__(None, None, None)


_PUBLISH_RETRY_DELAYS_S = (0.1, 0.3, 1.0)

# Connection/channel drops worth retrying (rabbitmq pod briefly out of the
# Service endpoints, broker restarting, etc.).
_PUBLISH_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    aio_pika.exceptions.AMQPConnectionError,
    aio_pika.exceptions.ChannelClosed,
)

# Subclasses of AMQPConnectionError that are NOT transient — auth/protocol
# failures will not get better with retry, so propagate immediately.
_PUBLISH_FATAL_ERRORS: tuple[type[BaseException], ...] = (
    aio_pika.exceptions.AuthenticationError,
    aio_pika.exceptions.ProbableAuthenticationError,
    aio_pika.exceptions.IncompatibleProtocolError,
    aio_pika.exceptions.ProtocolSyntaxError,
)


def _is_transient_publish_error(exc: BaseException) -> bool:
    if isinstance(exc, _PUBLISH_FATAL_ERRORS):
        return False
    return isinstance(exc, _PUBLISH_TRANSIENT_ERRORS)


async def _publish_once(
    queue_name: str,
    message: dict[str, Any],
    priority: int,
    *,
    message_id: str | None = None,
    headers: dict[str, Any] | None = None,
) -> None:
    async with rabbitmq.get_connection() as connection:
        channel = await connection.channel()
        try:
            dead_letter_exchange = f"{queue_name}-dlx"

            await channel.declare_exchange(
                dead_letter_exchange,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )

            dlq = await channel.declare_queue(
                f"{queue_name}-poison",
                durable=True,
            )
            await dlq.bind(dead_letter_exchange, routing_key=queue_name)

            await channel.declare_queue(
                queue_name,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": dead_letter_exchange,
                    "x-dead-letter-routing-key": queue_name,
                },
            )

            stable_id = str(message_id or infer_idempotency_key(queue_name, message))
            bounded_message_id = _bounded_message_id(stable_id)
            message_headers = dict(headers or {})
            message_headers.setdefault("x-idempotency-key", stable_id)
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    priority=priority,
                    message_id=bounded_message_id,
                    headers=_message_headers(
                        message,
                        queue_name,
                        message_id=stable_id,
                        headers=message_headers,
                    ),
                ),
                routing_key=queue_name,
            )

            logger.debug(f"Published message to {queue_name}")
        finally:
            await channel.close()


async def publish_message(
    queue_name: str,
    message: dict[str, Any],
    priority: int = 0,
    *,
    message_id: str | None = None,
    headers: dict[str, Any] | None = None,
) -> None:
    """
    Publish a message to a queue.

    Retries on transient broker errors (connection drops, channel close) so a
    brief readiness flap on the rabbitmq pod doesn't surface as a workflow
    failure. Real failures (auth, malformed message, broker rejecting the
    publish) are not retried and propagate immediately.

    Args:
        queue_name: Target queue name
        message: Message body (will be JSON encoded)
        priority: Message priority (0-9, higher = more important)
    """
    await rabbitmq.init_pools()
    last_exc: BaseException | None = None
    for attempt, delay in enumerate((*_PUBLISH_RETRY_DELAYS_S, None)):
        try:
            await _publish_once(queue_name, message, priority, message_id=message_id, headers=headers)
            return
        except Exception as exc:
            if not _is_transient_publish_error(exc):
                raise
            last_exc = exc
            if delay is None:
                break
            logger.warning(
                "Transient AMQP error publishing to %s (attempt %d/%d, sleeping %.2fs): %s",
                queue_name,
                attempt + 1,
                len(_PUBLISH_RETRY_DELAYS_S) + 1,
                delay,
                type(exc).__name__,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
