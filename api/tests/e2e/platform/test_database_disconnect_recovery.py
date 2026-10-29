"""A pool without pre-ping fails only the statement that meets a dead connection.

The database (or pooler) restarting kills every connection a pool holds. The
statement that first uses one fails; SQLAlchemy then invalidates the whole
pool, so every later checkout, including the other connections that were idle,
reconnects instead of failing in turn.
"""

import asyncio
import os
import uuid

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


async def _terminate(application_name: str) -> int:
    admin = create_async_engine(_direct_url(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            result = await connection.execute(
                sa.text("select count(pg_terminate_backend(pid)) from pg_stat_activity where application_name = :name"),
                {"name": application_name},
            )
            return int(result.scalar_one())
    finally:
        await admin.dispose()


async def _select_one(engine) -> int:
    async with engine.connect() as connection:
        return int((await connection.execute(sa.text("select 1"))).scalar_one())


@pytest.mark.asyncio
async def test_one_failed_statement_then_the_whole_pool_reconnects():
    pool = f"disconnect-test-{uuid.uuid4().hex[:8]}"
    engine = create_pool_engine(_direct_url(), pool=pool, pool_size=3, max_overflow=0, pool_timeout=10)
    try:
        # Warm three idle connections.
        await asyncio.gather(*(_select_one(engine) for _ in range(3)))
        assert await _terminate(f"bifrost:{pool}") == 3

        with pytest.raises(DBAPIError) as failed:
            await _select_one(engine)
        assert failed.value.connection_invalidated

        # The two other dead connections were invalidated with the pool, not handed out.
        assert await asyncio.gather(*(_select_one(engine) for _ in range(3))) == [1, 1, 1]
    finally:
        await engine.dispose()
