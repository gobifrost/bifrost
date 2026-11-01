"""Carry a short database unit of work through a brief database outage.

A PostgreSQL or pooler restart makes every statement fail for a few seconds,
and a CloudNativePG failover for about 30. A write whose result must not be
lost (a run's terminal status) retries its whole unit of work, in a fresh
session each time, until the database answers or its time budget is spent.
A transaction PostgreSQL aborts to break a deadlock is retried the same way.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import socket
import ssl
from asyncio import sleep
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)


class RetrySession(Protocol):
    """The part of ``AsyncSession`` the retry loop manages."""

    async def close(self) -> None: ...

    async def invalidate(self) -> None: ...


S = TypeVar("S", bound=RetrySession)
T = TypeVar("T")

# Class 08 is "connection exception"; 57P01-57P03 are the server shutting
# down, crashing or still starting. None of them says anything about the
# statement, only that the server could not run it.
_UNAVAILABLE_SQLSTATE_CLASS = "08"
_UNAVAILABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})
# deadlock_detected and serialization_failure: PostgreSQL aborted this
# transaction to let a conflicting one finish. Its documented remedy is to
# run the transaction again, which the retry does from the start.
_CONFLICT_SQLSTATES = frozenset({"40P01", "40001"})

# A connect that fails with one of these never reached a server: asyncio
# raises a plain OSError for them ("Connect call failed").
_UNREACHABLE_ERRNOS = frozenset(
    {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTDOWN}
)

# 0.5 s doubling to an 8 s cap reaches the database within a second or two
# of a few-second pooler or PostgreSQL restart (0.5 + 1 + 2 + 4 s) and keeps
# trying through a ~30 s failover. The budget is wall-clock time from the
# first attempt, covering connects, statements and lock waits: an attempt
# still running when it ends is cut off. 60 s is far under the stale-run
# sweep (a run's max_run_timeout, 30 min by default, plus 5 min), which takes
# over if the database stays away longer.
RETRY_INITIAL_DELAY_SECONDS = 0.5
RETRY_MAX_DELAY_SECONDS = 8.0
RETRY_BUDGET_SECONDS = 60.0
# Releasing an attempt's session never takes longer than this; asyncpg's
# own graceful close gives up after 2 s and then drops the socket.
CLEANUP_SECONDS = 2.0


class RetryBudget:
    """Wall-clock time for every write that records one outcome.

    It starts when first used, so writes that share it (a run's result
    write, then the failure write that follows it) wait 60 s in total, not
    60 s each.
    """

    def __init__(self, seconds: float = RETRY_BUDGET_SECONDS) -> None:
        self._seconds = seconds
        self._deadline: float | None = None

    def deadline(self) -> float:
        """The loop time at which retrying stops."""
        if self._deadline is None:
            self._deadline = asyncio.get_running_loop().time() + self._seconds
        return self._deadline


def is_retryable_database_error(exc: BaseException) -> bool:
    """Whether running the same unit of work again can succeed.

    True when the database could not be reached (a connection SQLAlchemy
    invalidated as disconnected, a network failure while connecting:
    refused, reset, unreachable, timed out, name resolution; the SQLSTATEs
    a server returns while restarting) and when PostgreSQL aborted the
    transaction to resolve a conflict (deadlock or serialization failure).
    False for everything that describes the work itself, such as constraint
    violations or bad SQL, and for local failures such as file permissions
    or TLS certificate errors.
    """
    if isinstance(exc, DBAPIError):
        if exc.connection_invalidated:
            return True
        # Errors raised on a live connection are wrapped; the driver's own
        # error, and its SQLSTATE, are on ``orig``.
        if exc.orig is None:
            return False
        exc = exc.orig
    if isinstance(exc, ssl.SSLError):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror)):
        return True
    if isinstance(exc, OSError):
        return exc.errno in _UNREACHABLE_ERRNOS
    sqlstate = getattr(exc, "sqlstate", None)
    return isinstance(sqlstate, str) and (
        sqlstate.startswith(_UNAVAILABLE_SQLSTATE_CLASS)
        or sqlstate in _UNAVAILABLE_SQLSTATES
        or sqlstate in _CONFLICT_SQLSTATES
    )


async def _release(db: RetrySession, *, discard: bool) -> None:
    """Hand back a session's connection within a bounded time.

    After a failed, timed-out or cancelled attempt the connection's state is
    unknown and the server may not answer, so it is discarded (invalidated)
    rather than rolled back: a graceful rollback against an unreachable
    server would wait on TCP, and SQLAlchemy shields a session's close from
    cancellation. Either way cleanup stops after CLEANUP_SECONDS.
    """
    try:
        async with asyncio.timeout(CLEANUP_SECONDS):
            if discard:
                await db.invalidate()
            else:
                await db.close()
    except Exception:
        logger.warning("Could not release a database session cleanly", exc_info=True)


async def run_with_database_retry(
    session_factory: Callable[[], S],
    unit: Callable[[S], Awaitable[T]],
    *,
    description: str,
    budget: RetryBudget,
) -> T:
    """Run ``unit`` in a fresh session, retrying while the database is away
    or after PostgreSQL aborted it to resolve a conflict.

    ``unit`` must do its whole read-check-write and commit, and must be safe
    to run again after an attempt whose COMMIT reached the server but whose
    acknowledgement was lost: a retry starts over in a new session, because
    a session whose connection failed cannot be used again and the rows may
    have changed meanwhile.

    Errors that are not :func:`is_retryable_database_error` are raised at once.
    Once ``budget`` is spent, the last attempt's error is raised (an attempt
    still in flight is cut off with ``TimeoutError``) and no write sharing
    the budget makes another attempt. Releasing each attempt's session is
    bounded too, so the call returns within the budget plus
    CLEANUP_SECONDS. Cancellation is never caught.
    """
    loop = asyncio.get_running_loop()
    deadline = budget.deadline()
    if loop.time() >= deadline:
        raise TimeoutError(f"Database retry budget spent before {description}")
    delay = RETRY_INITIAL_DELAY_SECONDS
    attempt = 1
    while True:
        db = session_factory()
        try:
            async with asyncio.timeout_at(deadline):
                result = await unit(db)
        except BaseException as exc:
            await _release(db, discard=True)
            if not isinstance(exc, Exception):
                raise
            remaining = deadline - loop.time()
            if remaining <= 0 or not is_retryable_database_error(exc):
                raise
            wait = min(delay, remaining)
            logger.warning(
                "Retryable database error during %s (attempt %d); retrying in %.1fs: %s",
                description,
                attempt,
                wait,
                exc,
            )
            await sleep(wait)
            if loop.time() >= deadline:
                raise
            delay = min(delay * 2, RETRY_MAX_DELAY_SECONDS)
            attempt += 1
        else:
            await _release(db, discard=False)
            return result
