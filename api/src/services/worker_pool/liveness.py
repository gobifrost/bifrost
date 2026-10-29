"""When an executor counts as lost (#769 stage 6).

An executor heartbeats every few seconds with the database's clock. It is
lost once it has not heartbeated for ``HEARTBEAT_LOST_AFTER`` while the
database was reachable: a control loop judges that only after it has itself
reached the database without a break for that long, so a database outage or
a control-plane restart never marks every executor lost at once.

The worker controller ends lost Job workers (and deletes their Jobs); the
dispatch reconciler ends lost static executors. Either way their runs end
visibly (``recovery.end_runs_of_lost_worker``) and are never replayed; a
lost executor's late results are refused (its claim is fenced).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text

from src.core.database import get_db_context
from src.models.orm.execution_workers import ExecutionWorker
from src.services.worker_pool.recovery import (
    LostRuns,
    announce_lost_runs,
    end_runs_of_lost_worker,
)

logger = logging.getLogger(__name__)

HEARTBEAT_LOST_AFTER = timedelta(seconds=30)


class Reachability:
    """How long one control loop has reached the database without a break.

    Only the database counts: a slow Kubernetes API or Redis must not keep a
    loop from ever judging an executor lost."""

    def __init__(self, connected_since: float | None = None) -> None:
        self._since = connected_since

    def connected(self) -> None:
        """A pass reached the database."""
        if self._since is None:
            self._since = time.monotonic()

    def broken(self) -> None:
        """A pass failed: start counting again."""
        self._since = None

    def steady(self) -> bool:
        """Connected long enough that every live executor has heartbeated."""
        return (
            self._since is not None
            and time.monotonic() - self._since >= HEARTBEAT_LOST_AFTER.total_seconds()
        )

    async def check(self) -> bool:
        """Reach the database once (each pass); whether it answered."""
        try:
            async with get_db_context() as db:
                await db.execute(text("SELECT 1"))
        except Exception:
            self.broken()
            return False
        self.connected()
        return True


def silent_executor():  # type: ignore[no-untyped-def]
    """SQL: an executor whose last heartbeat is older than HEARTBEAT_LOST_AFTER
    by the database's clock."""
    return ExecutionWorker.heartbeat_at.is_(None) | (
        ExecutionWorker.heartbeat_at < func.now() - HEARTBEAT_LOST_AFTER
    )


async def end_silent_static_executors() -> list[UUID]:
    """Mark silent static executors lost and end their runs; return the
    workflow runs ended. Safe on every control replica (SKIP LOCKED)."""
    ended = LostRuns()
    async with get_db_context() as db:
        workers = (
            await db.scalars(
                select(ExecutionWorker)
                .where(
                    ExecutionWorker.form == "static",
                    ExecutionWorker.state.in_(("starting", "ready", "draining")),
                    silent_executor(),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for worker in workers:
            logger.warning(
                "Executor lost: no heartbeat",
                extra={"worker_id": str(worker.id), "hostname": worker.hostname},
            )
            worker.state = "lost"
            worker.end_reason = "heartbeat_lost"
            worker.ended_at = func.now()
            await db.flush()
            ended.extend(await end_runs_of_lost_worker(db, worker.id))
        await db.commit()
    await announce_lost_runs(ended)
    return ended.workflow_runs


__all__ = [
    "HEARTBEAT_LOST_AFTER",
    "Reachability",
    "end_silent_static_executors",
    "silent_executor",
]
