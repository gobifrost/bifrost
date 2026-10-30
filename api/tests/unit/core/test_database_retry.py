"""A database write retried through a brief outage, and only through one."""

from __future__ import annotations

import asyncio
import errno
import socket
import ssl
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.database_retry import (
    RetryBudget,
    is_database_unavailable,
    run_with_database_retry,
)

REFUSED = ConnectionRefusedError(111, "Connect call failed ('10.96.0.12', 5432)")


class FakeSession:
    """Stands in for AsyncSession's ``close`` and ``invalidate``."""

    def __init__(self) -> None:
        self.released: str | None = None

    async def close(self) -> None:
        self.released = "closed"

    async def invalidate(self) -> None:
        self.released = "invalidated"


class StalledSession(FakeSession):
    """A session whose connection's server stopped answering: releasing it
    gracefully, or even invalidating it, never finishes."""

    async def close(self) -> None:
        await asyncio.Event().wait()

    async def invalidate(self) -> None:
        await asyncio.Event().wait()


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
        (OSError(errno.EHOSTUNREACH, "Connect call failed ('10.96.0.12', 5432)"), True),
        (ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"), True),
        (TimeoutError(), True),
        (socket.gaierror(socket.EAI_NONAME, "Name or service not known"), True),
        (PermissionError(errno.EACCES, "Permission denied"), False),
        (FileNotFoundError(errno.ENOENT, "No such file or directory"), False),
        (OSError(errno.EMFILE, "Too many open files"), False),
        (ssl.SSLCertVerificationError("certificate verify failed"), False),
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
        result = await run_with_database_retry(
            factory, unit, description="test write", budget=RetryBudget()
        )

    assert result == "written"
    assert seen == factory.sessions
    assert len(set(map(id, seen))) == 4
    # Failed attempts drop their connection; the one that worked returns it.
    assert [db.released for db in seen] == ["invalidated"] * 3 + ["closed"]
    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1.0, 2.0]


@pytest.mark.asyncio
async def test_backoff_doubles_to_its_cap():
    factory = FakeSessionFactory()
    unit, _ = _unit_failing([REFUSED] * 8)

    with patch("src.core.database_retry.sleep", AsyncMock()) as sleep:
        await run_with_database_retry(
            factory, unit, description="test write", budget=RetryBudget()
        )

    assert [call.args[0] for call in sleep.await_args_list] == [
        0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0, 8.0
    ]


@pytest.mark.asyncio
async def test_an_error_about_the_work_is_not_retried():
    factory = FakeSessionFactory()
    error = IntegrityError("INSERT", {}, asyncpg.exceptions.UniqueViolationError("dup"))
    unit, seen = _unit_failing([error])

    with patch("src.core.database_retry.sleep", AsyncMock()) as sleep:
        with pytest.raises(IntegrityError):
            await run_with_database_retry(
                factory, unit, description="test write", budget=RetryBudget()
            )

    assert len(seen) == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_gives_up_with_the_last_error_once_the_budget_is_spent():
    factory = FakeSessionFactory()
    unit, seen = _unit_failing([REFUSED] * 100)
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(ConnectionRefusedError):
        await run_with_database_retry(
            factory, unit, description="test write", budget=RetryBudget(0.05)
        )

    assert seen
    assert loop.time() - started < 1


@pytest.mark.asyncio
async def test_an_attempt_still_running_at_the_deadline_is_cut_off():
    factory = FakeSessionFactory()
    loop = asyncio.get_running_loop()
    started = loop.time()

    async def hung_unit(db: FakeSession) -> str:
        await asyncio.Event().wait()
        return "never"

    with pytest.raises(TimeoutError):
        await run_with_database_retry(
            factory, hung_unit, description="test write", budget=RetryBudget(0.05)
        )

    assert loop.time() - started < 1


@pytest.mark.asyncio
async def test_a_session_whose_cleanup_stalls_does_not_hold_the_call_open():
    loop = asyncio.get_running_loop()
    started = loop.time()

    async def hung_unit(db: StalledSession) -> str:
        await asyncio.Event().wait()
        return "never"

    with patch("src.core.database_retry.CLEANUP_SECONDS", 0.05):
        with pytest.raises(TimeoutError):
            await run_with_database_retry(
                StalledSession, hung_unit, description="test write", budget=RetryBudget(0.05)
            )

    assert loop.time() - started < 1


@pytest.mark.asyncio
async def test_writes_sharing_a_spent_budget_make_no_further_attempt():
    budget = RetryBudget(0.05)
    failing, _ = _unit_failing([REFUSED] * 100)
    with pytest.raises(ConnectionRefusedError):
        await run_with_database_retry(
            FakeSessionFactory(), failing, description="result write", budget=budget
        )

    later, seen = _unit_failing([])
    with pytest.raises(TimeoutError):
        await run_with_database_retry(
            FakeSessionFactory(), later, description="failure write", budget=budget
        )

    assert seen == []


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
            run_with_database_retry(
                factory, unit, description="test write", budget=RetryBudget()
            )
        )
        await backing_off.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(seen) == 1
