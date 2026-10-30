"""Carry a short database unit of work through a brief database outage.

A PostgreSQL or pooler restart makes every statement fail for a few seconds,
and a CloudNativePG failover for about 30. A write whose result must not be
lost (a run's terminal status) retries its whole unit of work, in a fresh
session each time, until the database answers or the backoff budget is spent.
"""

from __future__ import annotations

import logging
from asyncio import sleep
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import TypeVar

from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

S = TypeVar("S")
T = TypeVar("T")

# Class 08 is "connection exception"; 57P01-57P03 are the server shutting
# down, crashing or still starting. None of them says anything about the
# statement, only that the server could not run it.
_UNAVAILABLE_SQLSTATE_CLASS = "08"
_UNAVAILABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})

# 0.5 s doubling to an 8 s cap reaches the database within a second or two
# of a few-second pooler or PostgreSQL restart (0.5 + 1 + 2 + 4 s) and keeps
# trying through a ~30 s failover. Waiting stops after 60 s in total: far
# under the stale-run sweep (a run's max_run_timeout, 30 min by default,
# plus 5 min), which takes over if the database stays away longer.
RETRY_INITIAL_DELAY_SECONDS = 0.5
RETRY_MAX_DELAY_SECONDS = 8.0
RETRY_BUDGET_SECONDS = 60.0


def is_database_unavailable(exc: BaseException) -> bool:
    """Whether ``exc`` means the database could not be reached.

    True for a connection SQLAlchemy invalidated as disconnected, a socket
    error while connecting (refused, unreachable, timed out), and the
    SQLSTATEs a server returns while restarting. False for everything that
    describes the work itself, such as constraint violations or bad SQL.
    """
    if isinstance(exc, DBAPIError):
        if exc.connection_invalidated:
            return True
        # Errors raised on a live connection are wrapped; the driver's own
        # error, and its SQLSTATE, are on ``orig``.
        if exc.orig is None:
            return False
        exc = exc.orig
    if isinstance(exc, OSError):
        return True
    sqlstate = getattr(exc, "sqlstate", None)
    return isinstance(sqlstate, str) and (
        sqlstate.startswith(_UNAVAILABLE_SQLSTATE_CLASS)
        or sqlstate in _UNAVAILABLE_SQLSTATES
    )


async def run_with_database_retry(
    session_factory: Callable[[], AbstractAsyncContextManager[S]],
    unit: Callable[[S], Awaitable[T]],
    *,
    description: str,
) -> T:
    """Run ``unit`` in a fresh session, retrying while the database is away.

    ``unit`` must do its whole read-check-write and commit, because a retry
    starts over in a new session: a session whose connection failed cannot
    be used again, and the row may have changed in the meantime. Errors that
    are not :func:`is_database_unavailable` are raised at once. When the
    backoff budget is spent, the last error is raised. Cancellation is never
    caught.
    """
    delay = RETRY_INITIAL_DELAY_SECONDS
    waited = 0.0
    attempt = 1
    while True:
        try:
            async with session_factory() as db:
                return await unit(db)
        except Exception as exc:
            remaining = RETRY_BUDGET_SECONDS - waited
            if not is_database_unavailable(exc) or remaining <= 0:
                raise
            wait = min(delay, remaining)
            logger.warning(
                "Database unavailable during %s (attempt %d); retrying in %.1fs: %s",
                description,
                attempt,
                wait,
                exc,
            )
            await sleep(wait)
            waited += wait
            delay = min(delay * 2, RETRY_MAX_DELAY_SECONDS)
            attempt += 1
