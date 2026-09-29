"""
Authoritative liveness check for an execution.

An execution-scoped engine token (see `src.core.security.mint_engine_token`)
must stop working once its execution is no longer running. The execution
row's status in Postgres is the single source of truth for this -- unlike
the Redis active-execution lease, which is best-effort tracking that the
platform deliberately tolerates losing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from src.models.enums import LIVE_EXECUTION_STATUSES
from src.models.orm.executions import Execution

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def is_execution_live(db: "AsyncSession", execution_id: str | UUID) -> bool:
    """Return True if `execution_id` refers to an execution row whose status
    is in `LIVE_EXECUTION_STATUSES`.

    Returns False for a missing row or a malformed id. Callers are
    responsible for their own error handling (e.g. failing closed on a
    database error).
    """
    if isinstance(execution_id, UUID):
        parsed_id = execution_id
    else:
        try:
            parsed_id = UUID(execution_id)
        except (ValueError, AttributeError, TypeError):
            return False

    result = await db.execute(select(Execution.status).where(Execution.id == parsed_id))
    status = result.scalar_one_or_none()
    if status is None:
        return False
    return status in LIVE_EXECUTION_STATUSES
