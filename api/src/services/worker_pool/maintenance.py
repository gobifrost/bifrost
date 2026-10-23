"""Keep the executor registry free of dead rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update

from src.core.database import get_db_context
from src.models.orm.execution_workers import ExecutionWorker
from src.scheduler.registry import ScheduledTaskOutcome

ENDED_RETENTION = timedelta(days=7)
# Static executors have no controller watching their Job, so silence alone
# ends them. Job workers are ended by the controller much sooner.
STATIC_SILENT_AFTER = timedelta(minutes=10)


async def prune_execution_workers() -> ScheduledTaskOutcome:
    now = datetime.now(timezone.utc)
    async with get_db_context() as db:
        silent = (
            await db.execute(
                update(ExecutionWorker)
                .where(
                    ExecutionWorker.form == "static",
                    ExecutionWorker.state.in_(("ready", "draining")),
                    ExecutionWorker.heartbeat_at < now - STATIC_SILENT_AFTER,
                )
                .values(state="lost", end_reason="heartbeat_lost", ended_at=now)
            )
        ).rowcount
        pruned = (
            await db.execute(
                delete(ExecutionWorker).where(
                    ExecutionWorker.state.in_(("retired", "lost")),
                    ExecutionWorker.ended_at < now - ENDED_RETENTION,
                )
            )
        ).rowcount
    return ScheduledTaskOutcome(
        summary=f"Marked {silent} silent executors lost; pruned {pruned} ended executors"
    )


__all__ = ["prune_execution_workers"]
