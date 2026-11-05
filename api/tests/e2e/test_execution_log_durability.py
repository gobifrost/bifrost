"""Native execution-log durability contracts. Zero dispatched jobs.

Delivery is at-least-once, not exactly-once: a crash after DB commit but
before Redis acknowledgement can replay a log. This deliberately adds no
deduplication contract.
"""

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost._logging import (
    flush_logs_to_postgres,
    recover_terminal_execution_logs,
    stage_logs_to_postgres,
)
from src.core.cache import cleanup_execution_cache, get_redis
from src.core.cache.keys import execution_log_recovery_cursor_key, execution_logs_stream_key
from src.models.enums import ExecutionStatus
from src.models.orm import Execution, ExecutionLog

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def entry(message: str) -> dict[str, str]:
    return {
        "timestamp": "2026-10-28T13:00:00+00:00",
        "level": "INFO",
        "message": message,
        "metadata": "{}",
    }


@pytest_asyncio.fixture
async def log_stream(async_session_factory):
    execution_id = uuid4()
    key = execution_logs_stream_key(str(execution_id))
    async with get_redis() as redis:
        try:
            async with async_session_factory() as session:
                session.add(Execution(
                    id=execution_id,
                    workflow_name="log-durability",
                    executed_by_name="log-durability-test",
                ))
                await session.commit()
            await redis.xadd(key, entry("original"))
            yield execution_id, key, redis
        finally:
            try:
                await redis.delete(key)
            finally:
                async with async_session_factory() as session:
                    await session.execute(delete(ExecutionLog).where(
                        ExecutionLog.execution_id == execution_id,
                    ))
                    await session.execute(delete(Execution).where(
                        Execution.id == execution_id,
                    ))
                    await session.commit()


async def messages(factory, execution_id):
    async with factory() as session:
        return list(await session.scalars(select(ExecutionLog.message).where(
            ExecutionLog.execution_id == execution_id,
        ).order_by(ExecutionLog.id)))


async def test_caller_rollback_preserves_source_and_unrelated_state(
    log_stream, async_session_factory,
):
    execution_id, key, redis = log_stream
    async with async_session_factory() as session:
        parent = await session.get(Execution, execution_id)
        parent.workflow_name = "must-not-commit"
        receipt = await stage_logs_to_postgres(execution_id, session)
        assert receipt.count == 1
        await session.flush()
        await session.rollback()
    assert await messages(async_session_factory, execution_id) == []
    async with async_session_factory() as session:
        assert (await session.get(Execution, execution_id)).workflow_name == "log-durability"
    assert [data["message"] for _, data in await redis.xrange(key)] == ["original"]


async def test_concurrent_append_survives_owned_flush_and_terminal_cleanup(
    log_stream, async_session_factory, monkeypatch,
):
    execution_id, key, producer = log_stream
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)
    real_xrange = Redis.xrange

    async def append_after_snapshot(reader, name, *args, **kwargs):
        snapshot = await real_xrange(reader, name, *args, **kwargs)
        if name == key:
            assert reader is not producer
            await producer.xadd(key, entry("late"))
        return snapshot

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "xrange", append_after_snapshot)
        assert await flush_logs_to_postgres(execution_id) == 1
    await cleanup_execution_cache(str(execution_id))
    assert await messages(async_session_factory, execution_id) == ["original"]
    assert [data["message"] for _, data in await producer.xrange(key)] == ["late"]


async def test_owned_flush_allocates_after_existing_direct_log_rows(
    log_stream, async_session_factory, monkeypatch,
):
    """A stream replay follows logs written directly by existing producers."""
    execution_id, _key, _redis = log_stream
    async with async_session_factory() as session:
        session.add(ExecutionLog(
            execution_id=execution_id,
            level="INFO",
            message="direct",
            sequence=7,
        ))
        await session.commit()

    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)
    assert await flush_logs_to_postgres(execution_id) == 1

    async with async_session_factory() as session:
        rows = (await session.execute(
            select(ExecutionLog.message, ExecutionLog.sequence)
            .where(ExecutionLog.execution_id == execution_id)
            .order_by(ExecutionLog.sequence, ExecutionLog.id)
        )).all()
    assert rows == [("direct", 7), ("original", 8)]


async def test_owned_commit_failure_retains_source(log_stream, async_session_factory, monkeypatch):
    execution_id, key, redis = log_stream
    async def fail_commit(self):
        raise RuntimeError("commit failed")

    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", fail_commit)
        assert await flush_logs_to_postgres(execution_id) == 0
    assert await messages(async_session_factory, execution_id) == []
    assert [data["message"] for _, data in await redis.xrange(key)] == ["original"]


async def test_failed_ack_replays_at_least_once(log_stream, async_session_factory, monkeypatch):
    execution_id, key, redis = log_stream
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)

    async def failed_ack(*args, **kwargs):
        raise ConnectionError("ack unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "xdel", failed_ack)
        assert await flush_logs_to_postgres(execution_id) == 1
    assert await messages(async_session_factory, execution_id) == ["original"]
    assert len(await redis.xrange(key)) == 1
    assert await flush_logs_to_postgres(execution_id) == 1
    assert await messages(async_session_factory, execution_id) == ["original", "original"]
    assert await redis.xrange(key) == []
    assert await flush_logs_to_postgres(execution_id) == 0


async def test_competing_flushers_allow_duplicates_without_loss(
    log_stream, async_session_factory, monkeypatch,
):
    execution_id, key, redis = log_stream
    monkeypatch.setattr("src.core.database.get_session_factory", lambda: async_session_factory)
    real_xrange = Redis.xrange
    both_read = asyncio.Event()
    readers = 0

    async def snapshot_barrier(reader, name, *args, **kwargs):
        nonlocal readers
        snapshot = await real_xrange(reader, name, *args, **kwargs)
        if name == key:
            readers += 1
            if readers == 2:
                both_read.set()
            await both_read.wait()
        return snapshot

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "xrange", snapshot_barrier)
        counts = await asyncio.gather(
            flush_logs_to_postgres(execution_id), flush_logs_to_postgres(execution_id),
        )
    assert counts == [1, 1]
    assert await messages(async_session_factory, execution_id) == ["original", "original"]
    assert await redis.xrange(key) == []


@pytest.mark.parametrize(
    "malformed_entry",
    [
        {"level": "X" * 21},
        {"metadata": "[]"},
        {"metadata": '{"value": NaN}'},
    ],
)
async def test_terminal_recovery_flushes_later_valid_entries_after_malformed_source(
    log_stream, async_session_factory, malformed_entry,
):
    execution_id, key, redis = log_stream
    await redis.xadd(key, {"timestamp": "not-a-time", "metadata": "{}"})
    await redis.xadd(key, {**entry("invalid"), **malformed_entry})
    await redis.xadd(key, entry("later-valid"))
    async with async_session_factory() as session:
        await session.execute(update(Execution).where(Execution.id == execution_id).values(status="Success"))
        await session.commit()

    result = await recover_terminal_execution_logs([execution_id])

    assert result == {"scanned": 0, "terminal": 1, "flushed": 2}
    assert await messages(async_session_factory, execution_id) == ["original", "later-valid"]
    assert len(await redis.xrange(key)) == 2


async def test_bounded_recovery_advances_past_malformed_and_live_streams(
    log_stream, async_session_factory, monkeypatch,
):
    execution_id, key, redis = log_stream
    second_terminal = uuid4()
    second_key = execution_logs_stream_key(str(second_terminal))
    running = uuid4()
    running_key = execution_logs_stream_key(str(running))
    malformed_key = "bifrost:logs:not-a-uuid"
    try:
        async with async_session_factory() as session:
            await session.execute(
                update(Execution).where(Execution.id == execution_id).values(status=ExecutionStatus.SUCCESS)
            )
            session.add_all([
                Execution(
                    id=second_terminal,
                    workflow_name="log-durability",
                    executed_by_name="log-durability-test",
                    status=ExecutionStatus.SUCCESS,
                ),
                Execution(
                    id=running,
                    workflow_name="log-durability",
                    executed_by_name="log-durability-test",
                    status=ExecutionStatus.RUNNING,
                ),
            ])
            await session.commit()
        await redis.xadd(second_key, entry("second-terminal"))
        await redis.xadd(running_key, entry("live"))
        await redis.xadd(malformed_key, entry("malformed-key"))
        await redis.delete(execution_log_recovery_cursor_key())

        async def two_pages(reader, cursor=0, *args, **kwargs):
            if int(cursor) == 0:
                return 1, [malformed_key, key]
            assert int(cursor) == 1
            return 0, [second_key, running_key]

        with monkeypatch.context() as patch:
            patch.setattr(Redis, "scan", two_pages)
            first = await recover_terminal_execution_logs(limit=2)
            assert first == {"scanned": 2, "terminal": 1, "flushed": 1}
            assert await redis.get(execution_log_recovery_cursor_key()) == "1"
            assert await redis.xrange(malformed_key)
            assert [data["message"] for _, data in await redis.xrange(running_key)] == ["live"]
            second = await recover_terminal_execution_logs(limit=2)

        assert second == {"scanned": 2, "terminal": 1, "flushed": 1}
        assert await redis.get(execution_log_recovery_cursor_key()) == "0"
        assert await messages(async_session_factory, execution_id) == ["original"]
        assert await messages(async_session_factory, second_terminal) == ["second-terminal"]
        assert [data["message"] for _, data in await redis.xrange(running_key)] == ["live"]
        assert await redis.xrange(malformed_key)
    finally:
        await redis.delete(second_key, running_key, malformed_key, execution_log_recovery_cursor_key())
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionLog).where(
                ExecutionLog.execution_id.in_([second_terminal, running]),
            ))
            await session.execute(delete(Execution).where(Execution.id.in_([second_terminal, running])))
            await session.commit()
