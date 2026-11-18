"""Answer a request PostgreSQL aborted to break a deadlock with a clear error.

When two transactions wait on each other, PostgreSQL detects the cycle
(after ``deadlock_timeout``, one second by default) and aborts one of them
with ``deadlock_detected``. Nothing that transaction wrote is kept, so the
right answer is "nothing was saved; try again" rather than a generic 500.

A route can also name the error for a unique violation: two requests that
both pass a "does this already exist?" check and then both insert. The
loser's transaction is rolled back, so its answer is the conflict the check
would have given had it run second.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

from src.core.database_retry import is_transaction_conflict, is_unique_violation

logger = logging.getLogger(__name__)


def conflict_aware_route(
    on_conflict: Callable[[], Exception],
    *,
    on_duplicate: Callable[[], Exception] | None = None,
) -> type[APIRoute]:
    """An ``APIRoute`` class that turns a transaction conflict into the
    exception ``on_conflict`` builds, and a unique violation into the one
    ``on_duplicate`` builds when given (handled by the app's exception
    handlers), and logs it, so the rate of these is visible."""

    class ConflictAwareRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Any]:
            handler = super().get_route_handler()

            async def route(request: Request) -> Response:
                try:
                    return await handler(request)
                except Exception as exc:
                    if on_duplicate is not None and is_unique_violation(exc):
                        logger.warning(
                            "Unique violation on %s %s; nothing was saved: %s",
                            request.method,
                            request.url.path,
                            exc,
                        )
                        raise on_duplicate() from None
                    if not is_transaction_conflict(exc):
                        raise
                    logger.warning(
                        "Transaction conflict on %s %s; nothing was saved: %s",
                        request.method,
                        request.url.path,
                        exc,
                    )
                    raise on_conflict() from None

            return route

    return ConflictAwareRoute
