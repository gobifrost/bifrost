"""
Resilient Redis Pub/Sub Listener

Provides automatic reconnection with exponential backoff for Redis pub/sub
subscriptions. Use this to ensure listeners stay connected even when Redis
restarts or network issues occur.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from redis.asyncio import Redis
from redis.asyncio.client import PubSub


logger = logging.getLogger(__name__)


@dataclass
class ResilientPubSubListener:
    """
    Wraps Redis pub/sub with automatic reconnection and exponential backoff.

    Handles connection drops gracefully by reconnecting with increasing delays,
    ensuring the listener stays active even during Redis restarts or network issues.

    Usage:
        listener = ResilientPubSubListener(
            redis_url="redis://localhost:6379",
            channels=["bifrost:scheduler:reindex", "bifrost:scheduler:git-sync"],
            on_message=handle_message,
        )
        await listener.start()
        # Later...
        await listener.stop()

    For pattern subscriptions:
        listener = ResilientPubSubListener(
            redis_url="redis://localhost:6379",
            patterns=["bifrost:*"],
            on_message=handle_message,
        )
    """

    redis_url: str
    on_message: Callable[[str, dict], Awaitable[None]]
    channels: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)

    # Backoff configuration
    initial_backoff: float = 1.0  # Initial retry delay in seconds
    max_backoff: float = 30.0  # Maximum retry delay
    backoff_multiplier: float = 2.0  # Multiplier for each retry
    extended_failure_threshold: int = 5  # Log ERROR after this many failures

    # Internal state
    _redis: Redis | None = field(default=None, init=False)
    _pubsub: PubSub | None = field(default=None, init=False)
    _listener_task: asyncio.Task | None = field(default=None, init=False)
    _running: bool = field(default=False, init=False)
    _consecutive_failures: int = field(default=0, init=False)

    async def start(self) -> asyncio.Task:
        """
        Start the listener with automatic reconnection.

        Returns:
            The background task running the listener loop.
        """
        if self._running:
            raise RuntimeError("Listener is already running")

        self._running = True
        self._listener_task = asyncio.create_task(self._listener_loop())
        return self._listener_task

    async def stop(self) -> None:
        """Stop the listener and clean up resources."""
        self._running = False

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                # Expected — we just cancelled the task
                pass
            self._listener_task = None

        await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up Redis connections."""
        if self._pubsub:
            try:
                await self._pubsub.close()
            except Exception as e:
                # Already-closed pubsub or transient Redis failure — best-effort cleanup
                logger.debug(f"pubsub close failed during cleanup: {e}")
            self._pubsub = None

        if self._redis:
            try:
                await self._redis.close()
            except Exception as e:
                # Already-closed connection or transient Redis failure — best-effort cleanup
                logger.debug(f"redis close failed during cleanup: {e}")
            self._redis = None

    async def _connect(self) -> bool:
        """
        Establish Redis connection and subscribe to channels/patterns.

        Returns:
            True if connection was successful, False otherwise.
        """
        try:
            # Clean up any existing connections first
            await self._cleanup()

            # Create new connection
            self._redis = Redis.from_url(self.redis_url)
            self._pubsub = self._redis.pubsub()

            # Subscribe to channels
            for channel in self.channels:
                await self._pubsub.subscribe(channel)

            # Subscribe to patterns
            for pattern in self.patterns:
                await self._pubsub.psubscribe(pattern)

            return True

        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}")
            return False

    async def _listener_loop(self) -> None:
        """
        Main listener loop with reconnection logic.

        Continuously listens for messages and reconnects with exponential
        backoff when the connection drops.
        """
        backoff = self.initial_backoff

        while self._running:
            # Try to connect
            connected = await self._connect()

            if connected:
                # Reset backoff and failure count on successful connection
                if self._consecutive_failures > 0:
                    logger.info(
                        f"Redis pub/sub reconnected after {self._consecutive_failures} failures"
                    )
                backoff = self.initial_backoff
                self._consecutive_failures = 0

                # Listen for messages
                try:
                    await self._listen()
                except asyncio.CancelledError:
                    logger.debug("Redis listener cancelled")
                    return
                except Exception as e:
                    # Connection was lost
                    self._consecutive_failures += 1

                    if self._consecutive_failures >= self.extended_failure_threshold:
                        logger.exception(
                            f"Redis pub/sub error (failure #{self._consecutive_failures}): {e}"
                        )
                    else:
                        logger.warning(
                            f"Redis pub/sub connection lost, will reconnect in {backoff}s: {e}"
                        )
            else:
                # Connection failed
                self._consecutive_failures += 1

                if self._consecutive_failures >= self.extended_failure_threshold:
                    logger.error(
                        f"Redis pub/sub connection failed (attempt #{self._consecutive_failures})"
                    )
                else:
                    logger.warning(
                        f"Redis pub/sub connection failed, will retry in {backoff}s"
                    )

            # Don't retry if we're shutting down
            if not self._running:
                return

            # Wait with exponential backoff before reconnecting
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return

            # Increase backoff for next attempt (capped at max)
            backoff = min(backoff * self.backoff_multiplier, self.max_backoff)

    async def _listen(self) -> None:
        """
        Listen for messages and dispatch to callback.

        Raises:
            Exception: When the connection is lost or an error occurs.
        """
        if not self._pubsub:
            return

        while self._running:
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=0.5,  # Check for cancellation every 500ms
            )

            if message:
                await self._handle_message(message)

    async def _handle_message(self, message: dict) -> None:
        """
        Process a Redis pub/sub message and dispatch to callback.

        Args:
            message: Raw message from Redis pub/sub.
        """
        try:
            # Handle regular channel messages
            if message["type"] == "message":
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data = json.loads(message["data"])
                await self.on_message(channel, data)

            # Handle pattern messages
            elif message["type"] == "pmessage":
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                data = json.loads(message["data"])
                await self.on_message(channel, data)

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in pub/sub message: {message.get('data')}")
        except Exception as e:
            logger.exception(f"Error handling pub/sub message: {e}")

    def is_healthy(self) -> bool:
        """
        Check if the listener is healthy (running and connected).

        Returns:
            True if the listener is running with few recent failures.
        """
        return (
            self._running
            and self._listener_task is not None
            and not self._listener_task.done()
            and self._consecutive_failures < self.extended_failure_threshold
        )
