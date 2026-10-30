"""A database write retried through a brief outage, and only through one."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.database_retry import (
    RETRY_BUDGET_SECONDS,
    is_database_unavailable,
    run_with_database_retry,
)

REFUSED = ConnectionRefusedError(111, "Connect call failed ('10.96.0.12', 5432)")


class FakeSession:
    """Stands in for an AsyncSession used as ``async with factory() as db``."""

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeSessionFactory:
    """Mirrors ``async_sessionmaker.__call__``: a new session per call."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


def _unit_failing(errors: list[BaseException], result: str = "written"):
    """A unit of work that raises ``errors`` in order, then returns ``result``."""
    seen: list[FakeSession] = []

    async def unit(db: FakeSession) -> str:
        seen.append(db)
        if errors:
            raise errors.pop(0)
        return result

    return unit, seen


@pytest.mark.parametrize(
    ("exc", "unavailable"),
    [
        (REFUSED, True),
        (OSError(113, "Connect call failed ('10.96.0.12', 5432)"), True),
        (TimeoutError(), True),
        (asyncpg.exceptions.CannotConnectNowError("the database system is starting up"), True),
        (asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed"), True),
        (DBAPIError("SELECT 1", {}, Exception("closed"), connection_invalidated=True), True),
        (DBAPIError("SELECT 1", {}, asyncpg.exceptions.AdminShutdownError("shutdown")), True),
        (DBAPIError("SELECT 1", {}, asyncpg.exceptions.ConnectionFailureError("lost")), True),
        (IntegrityError("INSERT", {}, asyncpg.exceptions.UniqueViolationError("dup")), False),
        (DBAPIError("SELECT 1", {}, asyncpg.exceptions.UndefinedColumnError("no col")), False),
        (asyncpg.exceptions.UniqueViolationError("dup"), False),
        (ValueError("bad"), False),
    ],
)
def test_only_an_unreachable_database_counts_as_unavailable(exc, unavailable):
    assert is_database_unavailable(exc) is unavailable


@pytest.mark.asyncio
async def test_retries_in_a_fresh_session_until_the_database_answers():
    factory = FakeSessionFactory()
    unit, seen = _unit_failing([REFUSED, REFUSED, REFUSED])

    with patch("src.core.database_retry.sleep", AsyncMock()) as sleep:
        result = await run_with_database_retry(factory, unit, description="test write")

    assert result == "written"
    assert seen == factory.sessions
    assert len(set(map(id, seen))) == 4
    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1.0, 2.0]


@pytest.mark.asyncio
async def test_an_error_about_the_work_is_not_retried():
    factory = FakeSessionFactory()
    error = IntegrityError("INSERT", {}, asyncpg.exceptions.UniqueViolationError("dup"))
    unit, seen = _unit_failing([error])

    with patch("src.core.database_retry.sleep", AsyncMock()) as sleep:
        with pytest.raises(IntegrityError):
            await run_with_database_retry(factory, unit, description="test write")

    assert len(seen) == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_gives_up_with_the_last_error_once_the_budget_is_spent():
    factory = FakeSessionFactory()
    unit, seen = _unit_failing([REFUSED] * 100)

    with patch("src.core.database_retry.sleep", AsyncMock()) as sleep:
        with pytest.raises(ConnectionRefusedError):
            await run_with_database_retry(factory, unit, description="test write")

    delays = [call.args[0] for call in sleep.await_args_list]
    assert delays[:5] == [0.5, 1.0, 2.0, 4.0, 8.0]
    assert max(delays) == 8.0
    assert sum(delays) == RETRY_BUDGET_SECONDS
    assert len(seen) == len(delays) + 1


@pytest.mark.asyncio
async def test_cancellation_during_backoff_propagates():
    factory = FakeSessionFactory()
    unit, seen = _unit_failing([REFUSED] * 100)
    backing_off = asyncio.Event()

    async def _parked_sleep(seconds: float) -> None:
        backing_off.set()
        await asyncio.Event().wait()

    with patch("src.core.database_retry.sleep", _parked_sleep):
        task = asyncio.create_task(
            run_with_database_retry(factory, unit, description="test write")
        )
        await backing_off.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(seen) == 1
