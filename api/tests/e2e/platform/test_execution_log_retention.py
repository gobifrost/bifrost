"""Execution log streams retain a hard Redis expiry through acknowledgement."""

from typing import Awaitable, cast
from uuid import uuid4

import pytest

from bifrost._logging import (
    _DELETE_UNBOUNDED_STREAM_SCRIPT,
    LogFlushReceipt,
    acknowledge_staged_logs,
    append_log_to_stream,
    append_log_to_stream_async,
    close_thread_redis,
)
from src.core.cache import get_redis
from src.core.cache.keys import TTL_EXECUTION_LOGS, execution_logs_stream_key

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_expiry_recovery_only_deletes_streams_that_remain_unbounded():
    bounded_key = execution_logs_stream_key(str(uuid4()))
    repaired_key = execution_logs_stream_key(str(uuid4()))

    try:
        async with get_redis() as redis:
            bounded_entry = await redis.xadd(bounded_key, {"message": "pending"})
            await redis.expire(bounded_key, TTL_EXECUTION_LOGS)

            # Model a failed EXPIRE followed by a concurrent successful append
            # and expiry before the original caller begins recovery.
            failed_entry = await redis.xadd(repaired_key, {"message": "first"})
            async with redis.pipeline(transaction=True) as pipeline:
                pipeline.xadd(repaired_key, {"message": "concurrent"})
                pipeline.expire(repaired_key, TTL_EXECUTION_LOGS)
                concurrent_entry, expiry_applied = await pipeline.execute()
            assert expiry_applied is True

            assert (
                await cast(
                    Awaitable[int],
                    redis.eval(_DELETE_UNBOUNDED_STREAM_SCRIPT, 1, bounded_key),
                )
                == 0
            )
            assert (
                await cast(
                    Awaitable[int],
                    redis.eval(_DELETE_UNBOUNDED_STREAM_SCRIPT, 1, repaired_key),
                )
                == 0
            )

            assert await redis.xrange(bounded_key) == [
                (bounded_entry, {"message": "pending"})
            ]
            assert [entry_id for entry_id, _ in await redis.xrange(repaired_key)] == [
                failed_entry,
                concurrent_entry,
            ]
            for key in (bounded_key, repaired_key):
                ttl = await redis.ttl(key)
                assert 0 < ttl <= TTL_EXECUTION_LOGS
    finally:
        async with get_redis() as redis:
            await redis.delete(bounded_key, repaired_key)


@pytest.mark.asyncio
async def test_expiry_recovery_deletes_a_stream_that_remains_unbounded():
    stream_key = execution_logs_stream_key(str(uuid4()))

    try:
        async with get_redis() as redis:
            await redis.xadd(stream_key, {"message": "unbounded"})

            assert (
                await cast(
                    Awaitable[int],
                    redis.eval(_DELETE_UNBOUNDED_STREAM_SCRIPT, 1, stream_key),
                )
                == 1
            )
            assert await redis.exists(stream_key) == 0
    finally:
        async with get_redis() as redis:
            await redis.delete(stream_key)


@pytest.mark.asyncio
async def test_execution_log_streams_expire_after_sync_async_and_acknowledgement():
    sync_execution_id = str(uuid4())
    async_execution_id = str(uuid4())
    sync_key = execution_logs_stream_key(sync_execution_id)
    async_key = execution_logs_stream_key(async_execution_id)

    try:
        sync_entry_id = append_log_to_stream(
            sync_execution_id, "INFO", "sync retention proof"
        )
        async_entry_id = await append_log_to_stream_async(
            async_execution_id, "INFO", "async retention proof"
        )
        assert sync_entry_id is not None
        assert async_entry_id is not None

        async with get_redis() as redis:
            for key in (sync_key, async_key):
                ttl = await redis.ttl(key)
                assert 0 < ttl <= TTL_EXECUTION_LOGS

        await acknowledge_staged_logs(
            LogFlushReceipt(sync_execution_id, sync_key, (sync_entry_id,))
        )
        await acknowledge_staged_logs(
            LogFlushReceipt(async_execution_id, async_key, (async_entry_id,))
        )

        async with get_redis() as redis:
            for key in (sync_key, async_key):
                assert await redis.xlen(key) == 0
                ttl = await redis.ttl(key)
                assert 0 < ttl <= TTL_EXECUTION_LOGS
    finally:
        async with get_redis() as redis:
            await redis.delete(sync_key, async_key)
        close_thread_redis()
