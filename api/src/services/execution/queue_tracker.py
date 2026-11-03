"""Canonical queue snapshots for pending workflow executions.

``WorkDispatch`` is the durable queue authority.  A workflow remains visible
while its pending execution and unclaimed dispatch row both exist; positions
are ordered by the dispatch timestamp retained across a hand-back.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import Context
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.core.pubsub import ConnectionManager

# Advisory UI freshness, not installation-specific capacity configuration.
QUEUE_POSITION_REFRESH_INTERVAL_S = 1.0
QUEUE_POSITION_CHANNEL = "queue:positions"


@dataclass(frozen=True)
class QueuedExecution:
    """One durable workflow queue entry and its original queue timestamp."""

    execution_id: UUID
    queued_at: datetime


def _queued_dispatches() -> Select[tuple[UUID, datetime]]:
    from src.services.execution.workflow_dispatch import FAMILY

    return (
        select(WorkDispatch.subject_id, WorkDispatch.created_at)
        .join(Execution, Execution.id == WorkDispatch.subject_id)
        .where(
            WorkDispatch.family == FAMILY,
            Execution.status == ExecutionStatus.PENDING,
            WorkDispatch.payload["sync"].as_boolean().is_not(True),
        )
        .order_by(WorkDispatch.created_at.asc(), WorkDispatch.id.asc())
    )


async def get_queued_executions(db: AsyncSession) -> list[QueuedExecution]:
    """Return all asynchronously pending workflow dispatches in queue order."""
    rows = await db.execute(_queued_dispatches())
    return [
        QueuedExecution(execution_id=execution_id, queued_at=queued_at)
        for execution_id, queued_at in rows
    ]


async def get_queue_positions(db: AsyncSession, execution_ids: set[UUID]) -> dict[UUID, int]:
    """Rank the canonical queue, returning only executions someone is watching.

    Filter outside the window so unwatched entries still count toward position.
    The full queue never needs to be materialized in Python for a UI refresh.
    """
    if not execution_ids:
        return {}
    ranked = _queued_dispatches().order_by(None).add_columns(
        func.row_number().over(
            order_by=(WorkDispatch.created_at.asc(), WorkDispatch.id.asc()),
        ).label("position")
    ).subquery()
    rows = await db.execute(
        select(ranked.c.subject_id, ranked.c.position).where(ranked.c.subject_id.in_(execution_ids))
    )
    return {execution_id: int(position) for execution_id, position in rows}


async def refresh_queue_positions(manager: ConnectionManager) -> None:
    """Deliver queue metadata locally through the existing authorization guard."""
    watched: set[UUID] = set()
    for channel, sockets in tuple(manager.connections.items()):
        if sockets and channel.startswith("execution:"):
            try:
                watched.add(UUID(channel.split(":", 1)[1]))
            except ValueError:
                continue
    if not watched:
        return
    async with get_db_context() as db:
        positions = await get_queue_positions(db, watched)

    for execution_id, position in positions.items():
        await manager._send_local(
            f"execution:{execution_id}",
            {
                "type": "execution_queue_update",
                "executionId": str(execution_id),
                "queuePosition": position,
                "waitReason": "queued",
            },
        )


class QueuePositionRefresher:
    """One coalesced, lifespan-owned advisory refresh per API process.

    Requests do no I/O. Producers announce at most once per interval through the
    existing Redis transport; each API then serves only its own authorized UI
    subscriptions. Durable dispatch and execution status never wait on this.
    """

    def __init__(self, manager: ConnectionManager) -> None:
        self.manager = manager
        self._dirty = False
        self._announce = False
        self._task: asyncio.Task[None] | None = None

    def request(self, *, announce: bool = False) -> None:
        self._dirty = True
        self._announce |= announce
        if self._task is None:
            # UI work must not inherit a run's credentials or remote-owner fence.
            self._task = asyncio.create_task(
                self._run(), name="queue-position-refresh", context=Context(),
            )

    async def _wait_interval(self) -> None:
        await asyncio.sleep(QUEUE_POSITION_REFRESH_INTERVAL_S)

    async def _run(self) -> None:
        try:
            while self._dirty:
                await self._wait_interval()
                self._dirty = False
                announce, self._announce = self._announce, False
                try:
                    if announce:
                        await self.manager.broadcast(
                            QUEUE_POSITION_CHANNEL, {"type": "queue_positions_changed"},
                        )
                    await refresh_queue_positions(self.manager)
                except Exception:
                    logger.warning("Could not refresh queue positions", exc_info=True)
        finally:
            self._task = None

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._dirty = self._announce = False


def request_queue_position_refresh() -> None:
    """Record a committed queue change without delaying workflow dispatch."""
    from src.core.pubsub import manager

    manager.request_queue_position_refresh(announce=True)
