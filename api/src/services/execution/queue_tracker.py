"""Canonical queue snapshots for pending workflow executions.

``WorkDispatch`` is the durable queue authority.  A workflow remains visible
while its pending execution and unclaimed dispatch row both exist; positions
are ordered by the dispatch timestamp retained across a hand-back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedExecution:
    """One durable workflow queue entry and its original queue timestamp."""

    execution_id: UUID
    queued_at: datetime


async def get_queued_executions(db: AsyncSession) -> list[QueuedExecution]:
    """Return all asynchronously pending workflow dispatches in queue order."""
    from src.services.execution.workflow_dispatch import FAMILY

    rows = await db.execute(
        select(WorkDispatch.subject_id, WorkDispatch.created_at)
        .join(Execution, Execution.id == WorkDispatch.subject_id)
        .where(
            WorkDispatch.family == FAMILY,
            Execution.status == ExecutionStatus.PENDING,
            WorkDispatch.payload["sync"].as_boolean().is_not(True),
        )
        .order_by(WorkDispatch.created_at.asc(), WorkDispatch.id.asc())
    )
    return [
        QueuedExecution(execution_id=execution_id, queued_at=queued_at)
        for execution_id, queued_at in rows
    ]


async def publish_queue_positions() -> None:
    """Publish one best-effort position update for every durable queue entry."""
    async with get_db_context() as db:
        queued = await get_queued_executions(db)

    from src.core.pubsub import publish_execution_update

    for position, entry in enumerate(queued, start=1):
        try:
            await publish_execution_update(
                str(entry.execution_id),
                ExecutionStatus.PENDING.value,
                {"queuePosition": position, "waitReason": "queued"},
            )
        except Exception:
            logger.warning(
                "Could not publish queue position",
                extra={"execution_id": str(entry.execution_id)},
                exc_info=True,
            )
