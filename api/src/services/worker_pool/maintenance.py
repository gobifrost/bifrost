"""Keep the executor registry free of dead rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from src.core.database import get_db_context
from src.models.orm.execution_workers import ExecutionWorker
from src.scheduler.registry import ScheduledTaskOutcome

ENDED_RETENTION = timedelta(days=7)


async def prune_execution_workers() -> ScheduledTaskOutcome:
    """Delete executors that ended over ``ENDED_RETENTION`` ago (silent ones
    are ended by the control plane: ``worker_pool.liveness``)."""
    now = datetime.now(timezone.utc)
    async with get_db_context() as db:
        pruned = (
            await db.execute(
                delete(ExecutionWorker).where(
                    ExecutionWorker.state.in_(("retired", "lost")),
                    ExecutionWorker.ended_at < now - ENDED_RETENTION,
                )
            )
        ).rowcount
    return ScheduledTaskOutcome(summary=f"Pruned {pruned} ended executors")


__all__ = ["prune_execution_workers"]
