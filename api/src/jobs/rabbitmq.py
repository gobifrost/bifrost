"""
RabbitMQ Consumer Infrastructure

Provides the base consumer class and connection management for processing
background jobs from RabbitMQ queues.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import aio_pika
from aio_pika import IncomingMessage
from aio_pika.abc import AbstractRobustConnection, AbstractRobustChannel
from aio_pika.pool import Pool

from src.config import get_settings

logger = logging.getLogger(__name__)


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
        async with message.process(requeue=False):
            try:
                # Parse message body
                body = json.loads(message.body.decode())

                logger.info(
                    f"Processing message from {self.queue_name}",
                    extra={"message_id": message.message_id},
                )

                # Process the message
                await self.process_message(body)

                logger.info(
                    "Message processed successfully",
                    extra={"message_id": message.message_id},
                )

            except Exception as e:
                logger.error(
                    f"Error processing message from {self.queue_name}: {e}",
                    extra={
                        "message_id": message.message_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                # Message will be moved to DLQ due to requeue=False
                raise

    @abstractmethod
    async def process_message(self, body: dict[str, Any]) -> None:
        """
        Process a message from the queue.

        Must be implemented by subclasses.

        Args:
            body: Parsed message body
        """
        pass


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


async def publish_message(
    queue_name: str,
    message: dict[str, Any],
    priority: int = 0,
) -> None:
    """
    Publish a message to a queue.

    Args:
        queue_name: Target queue name
        message: Message body (will be JSON encoded)
        priority: Message priority (0-9, higher = more important)
    """
    await rabbitmq.init_pools()
    async with rabbitmq.get_connection() as connection:
        channel = await connection.channel()

        try:
            dead_letter_exchange = f"{queue_name}-dlx"

            # Declare dead letter exchange
            await channel.declare_exchange(
                dead_letter_exchange,
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )

            # Declare dead letter queue
            dlq = await channel.declare_queue(
                f"{queue_name}-poison",
                durable=True,
            )
            await dlq.bind(dead_letter_exchange, routing_key=queue_name)

            # Declare main queue with dead letter routing (matches consumer)
            await channel.declare_queue(
                queue_name,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": dead_letter_exchange,
                    "x-dead-letter-routing-key": queue_name,
                },
            )

            # Publish message
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    priority=priority,
                ),
                routing_key=queue_name,
            )

            logger.debug(f"Published message to {queue_name}")

        finally:
            await channel.close()
