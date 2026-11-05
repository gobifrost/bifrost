"""Durable state transitions for runs cancelled before dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


RUN_USER_DELETED_ERROR = "The user for this run was deleted"


async def cancel_unstarted_runs_for_deleted_user(session: AsyncSession, user_id: UUID) -> int:
    """Cancel scheduled and queued runs for ``user_id`` in the caller's transaction.

    A queued workflow has durable ``Pending`` state. The conditional update
    locks every matching row, so a dispatch can only proceed by first claiming
    it as ``Running``. The user deletion must call this before deleting the
    user row and commit both changes together.
    """
    result = await session.execute(
        update(Execution)
        .where(Execution.run_user_id == user_id)
        .where(Execution.status.in_((ExecutionStatus.SCHEDULED, ExecutionStatus.PENDING)))
        .values(
            status=ExecutionStatus.CANCELLED,
            error_message=RUN_USER_DELETED_ERROR,
            completed_at=datetime.now(timezone.utc),
        )
        .returning(Execution.id)
    )
    return len(result.scalars().all())


async def claim_pending_run_for_dispatch(session: AsyncSession, execution_id: UUID) -> bool:
    """Atomically claim a promoted run before the worker routes it.

    A false result means cancellation or another terminal transition won the
    race, so the worker must discard its queued message without changing the
    durable row.
    """
    claimed = await session.scalar(
        update(Execution)
        .where(Execution.id == execution_id)
        .where(Execution.status == ExecutionStatus.PENDING)
        .values(status=ExecutionStatus.RUNNING, started_at=datetime.now(timezone.utc))
        .returning(Execution.id)
    )
    return claimed is not None


