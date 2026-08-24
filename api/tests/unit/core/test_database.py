"""Database engine configuration tests."""

import asyncio

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from src.config import Settings
from src.core.database import get_engine, reset_db_state


def test_testing_engine_uses_null_pool() -> None:
    """Async connections cannot be reused across pytest's per-test loops."""
    reset_db_state()
    try:
        engine = get_engine(
            Settings(
                environment="testing",
                database_url="postgresql+asyncpg://test:test@localhost/test",
            )
        )
        assert isinstance(engine.pool, NullPool)
    finally:
        reset_db_state()


def test_testing_engine_connects_across_separate_event_loops() -> None:
    """The shared test engine must not retain a connection from a closed loop."""
    reset_db_state()
    engine = get_engine(Settings(environment="testing"))

    async def execute_probe() -> None:
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1

    try:
        asyncio.run(execute_probe())
        asyncio.run(execute_probe())
    finally:
        asyncio.run(engine.dispose())
        reset_db_state()
