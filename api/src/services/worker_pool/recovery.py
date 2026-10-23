"""End the runs a lost executor was running (never replay them)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.execution_workers import ExecutionWorkerClaim
from src.models.orm.executions import Execution
from src.services.execution.summary_dispatch import FAMILY as SUMMARY_FAMILY
from src.services.execution.summary_dispatch import stage_summary

logger = logging.getLogger(__name__)

EXECUTOR_LOST = (
    "The worker running this stopped before it finished. It was not retried, "
    "because it may already have taken effect."
)


async def end_runs_of_lost_worker(db: AsyncSession, worker_id: UUID) -> list[UUID]:
    """Fail the lost worker's runs that are still running; return the workflow runs ended.

    Runs it finished, or never managed to claim, are left as they are.
    Summaries it was generating are requested again instead: a summary has
    no effect outside its own run, so repeating it is safe.
    """
    claims = (
        await db.execute(
            select(ExecutionWorkerClaim.family, ExecutionWorkerClaim.subject_id).where(
                ExecutionWorkerClaim.worker_id == worker_id
            )
        )
    ).all()
    workflow_ids = [subject for family, subject in claims if family == "workflow_execution"]
    agent_ids = [subject for family, subject in claims if family == "agent_run"]
    summary_ids = [subject for family, subject in claims if family == SUMMARY_FAMILY]
    now = datetime.now(timezone.utc)
    ended: list[UUID] = []
    if workflow_ids:
        ended = list(
            (
                await db.scalars(
                    update(Execution)
                    .where(
                        Execution.id.in_(workflow_ids),
                        Execution.status.in_(
                            (ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING)
                        ),
                    )
                    .values(
                        status=ExecutionStatus.FAILED,
                        error_message=EXECUTOR_LOST,
                        completed_at=now,
                    )
                    .returning(Execution.id)
                )
            ).all()
        )
    if agent_ids:
        await db.execute(
            update(AgentRun)
            .where(AgentRun.id.in_(agent_ids), AgentRun.status.in_(("running", "cancelling")))
            .values(status="failed", error=EXECUTOR_LOST, completed_at=now)
        )
    if summary_ids:
        interrupted = (
            await db.execute(
                update(AgentRun)
                .where(AgentRun.id.in_(summary_ids), AgentRun.summary_status == "generating")
                .values(summary_status="pending")
                .returning(AgentRun.id, AgentRun.summary_backfill_job_id)
            )
        ).all()
        for run_id, backfill_job_id in interrupted:
            await stage_summary(db, run_id, backfill=backfill_job_id is not None)
    if ended or agent_ids:
        logger.warning(
            "Ended runs of a lost executor",
            extra={"worker_id": str(worker_id), "workflow_runs": len(ended)},
        )
    return ended


async def announce_ended_runs(execution_ids: list[UUID]) -> None:
    """Best-effort live update for runs ended after their executor was lost."""
    from src.core.pubsub import publish_execution_update

    for execution_id in execution_ids:
        try:
            await publish_execution_update(execution_id, "Failed", {"error": EXECUTOR_LOST})
        except Exception:
            logger.warning("Could not announce an ended run", exc_info=True)


__all__ = ["EXECUTOR_LOST", "announce_ended_runs", "end_runs_of_lost_worker"]
