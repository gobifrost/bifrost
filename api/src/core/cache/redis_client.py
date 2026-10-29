"""
Async Redis connection factory for Bifrost cache.

Borrows the API lifespan client from its owning event loop, while keeping
per-call Redis connections for worker threads with their own event loops.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class CacheError(Exception):
    """Base exception for cache operations."""

    pass


class CacheConnectionError(CacheError):
    """Failed to connect to Redis."""

    pass


class CacheOperationError(CacheError):
    """Redis operation failed."""

    pass


def _get_redis_url() -> str:
    """Get Redis URL from settings."""
    from src.config import get_settings

    return get_settings().redis_url


def _new_redis_client() -> redis.Redis:
    """Create a client with the established cache transport settings."""
    return redis.from_url(
        _get_redis_url(),
        decode_responses=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
    )


def _owns_shared_client() -> bool:
    """Whether this task may safely use the API lifespan's client."""
    return (
        _shared_client is not None
        and _shared_pid == os.getpid()
        and _shared_loop is asyncio.get_running_loop()
    )


@asynccontextmanager
async def get_redis() -> AsyncIterator[redis.Redis]:
    """
    Get an async Redis connection for a single operation.

    The API lifespan loop borrows its shared client. Other event loops create
    and close an isolated client so they never inherit loop-bound transports.

    Usage:
        async with get_redis() as r:
            value = await r.get("key")

    Yields:
        redis.Redis: Async Redis client connection

    Raises:
        CacheConnectionError: If connection fails
        CacheOperationError: If operation fails
    """
    if _owns_shared_client():
        assert _shared_client is not None
        try:
            yield _shared_client
        except redis.ConnectionError as e:
            logger.error(f"Redis connection failed: {e}")
            raise CacheConnectionError(f"Failed to connect to Redis: {e}") from e
        except redis.RedisError as e:
            logger.error(f"Redis operation failed: {e}")
            raise CacheOperationError(f"Redis error: {e}") from e
        return

    client: redis.Redis | None = None
    try:
        client = _new_redis_client()
        # Verify connection
        await client.ping()  # type: ignore[misc]
        yield client
    except redis.ConnectionError as e:
        logger.error(f"Redis connection failed: {e}")
        raise CacheConnectionError(f"Failed to connect to Redis: {e}") from e
    except redis.RedisError as e:
        logger.error(f"Redis operation failed: {e}")
        raise CacheOperationError(f"Redis error: {e}") from e
    finally:
        if client:
            await client.aclose()


# =============================================================================
# Shared Redis Client (for API/long-running processes)
# =============================================================================

# Global Redis client for API routes (shared connection)
_shared_client: redis.Redis | None = None
_shared_loop: asyncio.AbstractEventLoop | None = None
_shared_pid: int | None = None
_shared_initialization: tuple[int, asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def _get_shared_initialization_lock() -> asyncio.Lock:
    """Return a lock that is local to the current process and event loop."""
    global _shared_initialization
    loop = asyncio.get_running_loop()
    pid = os.getpid()
    if (
        _shared_initialization is None
        or _shared_initialization[0] != pid
        or _shared_initialization[1] is not loop
    ):
        _shared_initialization = (pid, loop, asyncio.Lock())
    return _shared_initialization[2]


async def initialize_shared_redis() -> redis.Redis:
    """Bind the API lifespan's Redis pool to its process and event loop."""
    global _shared_client, _shared_loop, _shared_pid

    async with _get_shared_initialization_lock():
        if _shared_client is not None:
            if _owns_shared_client():
                return _shared_client
            if _shared_pid != os.getpid():
                # A fork inherited a client bound to its parent. This process
                # establishes its own pool and never closes the parent's socket.
                _shared_client = None
                _shared_loop = None
                _shared_pid = None
            else:
                raise RuntimeError("Shared Redis client is bound to another event loop")

        client: redis.Redis | None = None
        try:
            client = _new_redis_client()
            await client.ping()  # type: ignore[misc]
        except BaseException as error:
            if client is not None:
                try:
                    await client.aclose()
                except BaseException:
                    logger.exception("Failed to close uninitialized Redis client")
            if isinstance(error, redis.ConnectionError):
                logger.error(f"Redis connection failed: {error}")
                raise CacheConnectionError(f"Failed to connect to Redis: {error}") from error
            if isinstance(error, redis.RedisError):
                logger.error(f"Redis operation failed: {error}")
                raise CacheOperationError(f"Redis error: {error}") from error
            raise

        assert client is not None
        _shared_client = client
        _shared_loop = asyncio.get_running_loop()
        _shared_pid = os.getpid()
        return client


async def get_shared_redis() -> redis.Redis:
    """
    Get the shared async Redis client for API routes.

    Unlike get_redis(), this reuses the pool bound to the API lifespan loop.
    Use this in API routes where we're in that main event loop.

    For SDK operations in worker threads, use get_redis() instead.

    Returns:
        redis.Redis: Shared async Redis client

    Raises:
        CacheConnectionError: If connection fails
    """
    return await initialize_shared_redis()


async def close_shared_redis() -> None:
    """Close the shared Redis client. Call on shutdown."""
    global _shared_client, _shared_loop, _shared_pid
    if _shared_client is None:
        return
    if not _owns_shared_client():
        logger.warning("Refusing to close shared Redis client from another loop or process")
        return

    client = _shared_client
    _shared_client = None
    _shared_loop = None
    _shared_pid = None
    await client.aclose()
    logger.debug("Closed shared Redis client")
