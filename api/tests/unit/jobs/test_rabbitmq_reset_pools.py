"""
Unit tests for RabbitMQConnection.reset_pools().

Regression coverage for the event-loop-pinning flake: the connection and
channel pools bind to whichever asyncio loop first touched them, so a test
running on a fresh function-scoped loop would reuse a pool pinned to a dead
loop and fail with ``RuntimeError: Event loop is closed`` on the next channel
open. ``reset_pools`` drops the references (without awaiting the dead loop) so
``init_pools`` rebuilds on the current loop.
"""

import pytest

from src.jobs.rabbitmq import RabbitMQConnection


@pytest.fixture
def conn():
    """The shared singleton, with its pools restored after each test.

    RabbitMQConnection is a singleton, so mutating its pools would leak into
    other tests in the same process; save and restore them.
    """
    c = RabbitMQConnection()
    saved = (c._connection_pool, c._channel_pool, c._pool_loop)
    try:
        yield c
    finally:
        c._connection_pool, c._channel_pool, c._pool_loop = saved


def test_reset_pools_clears_both_pools(conn):
    sentinel = object()
    conn._connection_pool = sentinel  # type: ignore[assignment]
    conn._channel_pool = sentinel  # type: ignore[assignment]

    conn.reset_pools()

    assert conn._connection_pool is None
    assert conn._channel_pool is None


def test_reset_pools_does_not_touch_stale_pool(conn):
    """reset_pools must be synchronous and must not call close().

    The whole point is to clear pools bound to an already-closed loop;
    awaiting close() on them would re-raise the very error we are clearing.
    A pool object that explodes on close() proves we never touch it.
    """

    class _Boom:
        def close(self):
            raise AssertionError("reset_pools must not call close() on the pool")

    conn._connection_pool = _Boom()  # type: ignore[assignment]
    conn._channel_pool = _Boom()  # type: ignore[assignment]

    # Synchronous call, no exception.
    conn.reset_pools()

    assert conn._connection_pool is None
    assert conn._channel_pool is None


def test_init_pools_rebuilds_on_loop_change(conn):
    """init_pools must not hand a new loop pools pinned to a dead loop.

    Regression coverage for the full-suite hang: an in-process publish on one
    function-scoped test loop built the singleton pools; the next test's loop
    short-circuited ``init_pools`` on the stale pool and deadlocked inside
    aio-pika's connection-ready wait. ``init_pools`` now records the building
    loop and rebuilds when the running loop differs.
    """
    import asyncio
    from unittest.mock import patch

    async def _fake_init(self):
        self._connection_pool = object()
        self._channel_pool = object()

    async def _scenario_same_loop():
        with patch.object(RabbitMQConnection, "_init_pools", _fake_init):
            await conn.init_pools()
            pool_a = conn._connection_pool
            assert pool_a is not None
            assert conn._pool_loop is asyncio.get_running_loop()
            await conn.init_pools()
            assert conn._connection_pool is pool_a
            return pool_a

    pool_on_first_loop = asyncio.run(_scenario_same_loop())

    async def _scenario_new_loop():
        with patch.object(RabbitMQConnection, "_init_pools", _fake_init):
            await conn.init_pools()
            assert conn._pool_loop is asyncio.get_running_loop()
            return conn._connection_pool

    pool_on_second_loop = asyncio.run(_scenario_new_loop())

    # The second loop must have rebuilt, not reused the first loop's pools.
    assert pool_on_second_loop is not None
    assert pool_on_second_loop is not pool_on_first_loop
