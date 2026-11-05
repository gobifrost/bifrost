"""A pool without pre-ping detects a database restart once, then reconnects.

The database (or pooler) restarting kills every connection a pool holds. The
next statement fails on a dead connection; SQLAlchemy then invalidates the
pool, so the connections that were idle beside it are replaced rather than
handed out to fail in turn. This covers serial detection followed by
concurrent recovery; connections already checked out when the database goes
away can each still fail once.
"""

import asyncio
import contextlib
import os
import time

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.database import create_pool_engine


def _direct_url() -> str:
    """The test database without pgbouncer, so terminating a backend kills this client's connection."""
    url = make_url(os.environ["BIFROST_DATABASE_URL"])
    return url.set(drivername="postgresql+asyncpg", host="postgres", port=5432).render_as_string(hide_password=False)


async def _warm(engine, count: int) -> list[int]:
    """Open ``count`` pool connections at once and return their backend pids.

    Holding them together makes them distinct connections; leaving the block
    returns them to the pool idle.
    """
    async with contextlib.AsyncExitStack() as stack:
        connections = [await stack.enter_async_context(engine.connect()) for _ in range(count)]
        return [int((await connection.execute(sa.text("select pg_backend_pid()"))).scalar_one()) for connection in connections]


async def _terminate(pids: list[int]) -> list[bool]:
    """Terminate these backends and wait until they have exited.

    pg_terminate_backend only signals the backend, so the next query could
    otherwise still reach a live connection.
    """
    admin = create_async_engine(_direct_url(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            terminated = list(
                (
                    await connection.execute(
                        sa.text("select pg_terminate_backend(pid) from unnest(cast(:pids as int[])) as pid"),
                        {"pids": pids},
                    )
                ).scalars()
            )
            deadline = time.monotonic() + 10
            while (
                await connection.execute(
                    sa.text("select count(*) from pg_stat_activity where pid = any(cast(:pids as int[]))"),
                    {"pids": pids},
                )
            ).scalar_one():
                assert time.monotonic() < deadline, "terminated backends did not exit"
                await asyncio.sleep(0.05)
            return terminated
    finally:
        await admin.dispose()


async def _select_one(engine) -> int:
    async with engine.connect() as connection:
        return int((await connection.execute(sa.text("select 1"))).scalar_one())


@pytest.mark.asyncio
async def test_one_failed_statement_then_the_whole_pool_reconnects():
    engine = create_pool_engine(_direct_url(), pool_size=3, max_overflow=0, pool_timeout=10)
    try:
        # Warm three idle connections.
        pids = await _warm(engine, 3)
        assert await _terminate(pids) == [True, True, True]

        with pytest.raises(DBAPIError) as failed:
            await _select_one(engine)
        assert failed.value.connection_invalidated

        # The two other dead connections were invalidated with the pool, not handed out.
        assert await asyncio.gather(*(_select_one(engine) for _ in range(3))) == [1, 1, 1]
    finally:
        await engine.dispose()
