"""End the runs a lost executor was running (never replay them)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.models.orm.executions import Execution
from src.services.execution.agent_run_service import announce_ended_agent_run
from src.services.memory_requests import WORKFLOW_REQUEST_MARGIN_BYTES, record_peak_growth
from src.services.execution.summary_dispatch import FAMILY as SUMMARY_FAMILY
from src.services.execution.summary_dispatch import stage_summary

logger = logging.getLogger(__name__)

EXECUTOR_LOST = (
    "The worker running this stopped before it finished. It was not retried, "
    "because it may already have taken effect."
)
# How far back an executor remembers what it started, and so how far back a
# claim it never started is looked for.
UNSTARTED_CLAIM_WINDOW = timedelta(minutes=10)
CLAIM_NOT_STARTED = (
    "The worker that took this lost its database connection before starting "
    "it. It was not run."
)


@dataclass
class LostRuns:
    """What a lost executor was running, to announce once its loss commits."""

    # Workflow runs this transaction failed.
    workflow_runs: list[UUID] = field(default_factory=list)
    # Agent runs it had claimed; announce_lost_runs ends those still live.
    agent_runs: list[UUID] = field(default_factory=list)

    def extend(self, other: LostRuns) -> None:
        self.workflow_runs.extend(other.workflow_runs)
        self.agent_runs.extend(other.agent_runs)


async def end_runs_of_lost_worker(
    db: AsyncSession, worker_id: UUID, *, out_of_memory: bool = False
) -> LostRuns:
    """Fail the lost worker's workflow runs that are still running; return
    those and its agent runs that had started.

    Runs it finished, or never managed to claim, are left as they are; an
    agent run it claimed but never started is still queued and goes to
    another worker. Its started agent runs are ended by
    :func:`announce_lost_runs` once this transaction commits, which also
    wakes their callers. Summaries it was generating are requested again
    instead: a summary has no effect outside its own run, so repeating it
    is safe.

    When the executor was stopped for memory (``out_of_memory``: Kubernetes
    reported it OOMKilled), its runs could not report what they used: each
    ended run's workflow learns the executor's whole budget (less the
    request margin), so its next run fits an executor alone; smaller runs
    then bring it back down. Any other loss (a crash, a forced stop, a lost
    node) teaches nothing.
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
    if agent_ids:
        agent_ids = list((await db.scalars(
            select(AgentRun.id).where(
                AgentRun.id.in_(agent_ids), AgentRun.status.in_(("running", "cancelling"))
            )
        )).all())
    summary_ids = [subject for family, subject in claims if family == SUMMARY_FAMILY]
    now = datetime.now(timezone.utc)
    ended: list[UUID] = []
    if workflow_ids:
        rows = (
            await db.execute(
                update(Execution)
                .where(
                    Execution.id.in_(workflow_ids),
                    Execution.status.in_((ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING)),
                )
                .values(
                    status=ExecutionStatus.FAILED,
                    error_message=EXECUTOR_LOST,
                    completed_at=now,
                )
                .returning(Execution.id, Execution.workflow_id)
            )
        ).all()
        ended = [execution_id for execution_id, _ in rows]
        budget = await db.scalar(
            select(ExecutionWorker.memory_budget_bytes).where(ExecutionWorker.id == worker_id)
        )
        if out_of_memory and budget is not None and budget > WORKFLOW_REQUEST_MARGIN_BYTES:
            for workflow_id in sorted({wf for _, wf in rows if wf is not None}):
                await record_peak_growth(
                    db, workflow_id, budget - WORKFLOW_REQUEST_MARGIN_BYTES, None
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
            extra={
                "worker_id": str(worker_id),
                "workflow_runs": len(ended),
                "agent_runs": len(agent_ids),
            },
        )
    return LostRuns(workflow_runs=ended, agent_runs=agent_ids)


async def announce_lost_runs(lost: LostRuns) -> None:
    """End a lost executor's live agent runs and wake every caller of its runs."""
    await announce_ended_runs(lost.workflow_runs)
    for run_id in lost.agent_runs:
        try:
            await announce_ended_agent_run(run_id, EXECUTOR_LOST)
        except Exception:
            logger.warning("Could not end a lost agent run", exc_info=True)


async def release_unstarted_claims(worker_id: UUID, started: set[str]) -> list[UUID]:
    """End this executor's recent claims on work it never started; return
    the workflow runs ended.

    For an executor whose claim committed but whose connection failed before
    it heard back. ``started`` holds the run ids and dispatch ids it started
    within ``UNSTARTED_CLAIM_WINDOW``; only claims that recent are examined.
    A workflow run's dispatch went with its claim, so the run is failed
    visibly; an agent run or summary is still queued and goes back to
    waiting.
    """
    from sqlalchemy import func

    from src.models.orm.work_dispatches import WorkDispatch

    now = datetime.now(timezone.utc)
    recent = ExecutionWorkerClaim.claimed_at >= func.now() - UNSTARTED_CLAIM_WINDOW
    async with get_db_context() as db:
        orphaned = [
            subject
            for subject in (
                await db.scalars(
                    select(ExecutionWorkerClaim.subject_id)
                    .join(Execution, Execution.id == ExecutionWorkerClaim.subject_id)
                    .where(
                        ExecutionWorkerClaim.worker_id == worker_id,
                        ExecutionWorkerClaim.family == "workflow_execution",
                        recent,
                        Execution.status == ExecutionStatus.RUNNING,
                    )
                )
            ).all()
            if str(subject) not in started
        ]
        ended: list[UUID] = []
        if orphaned:
            ended = list(
                (
                    await db.scalars(
                        update(Execution)
                        .where(
                            Execution.id.in_(orphaned),
                            Execution.status == ExecutionStatus.RUNNING,
                        )
                        .values(
                            status=ExecutionStatus.FAILED,
                            error_message=CLAIM_NOT_STARTED,
                            completed_at=now,
                        )
                        .returning(Execution.id)
                    )
                ).all()
            )
        unstarted = [
            (dispatch_id, subject)
            for dispatch_id, subject in (
                await db.execute(
                    select(WorkDispatch.id, WorkDispatch.subject_id).where(
                        WorkDispatch.assigned_worker_id == worker_id
                    )
                )
            ).all()
            if str(dispatch_id) not in started
        ]
        if unstarted:
            await db.execute(
                update(WorkDispatch)
                .where(WorkDispatch.id.in_([dispatch_id for dispatch_id, _ in unstarted]))
                .values(assigned_worker_id=None)
            )
        released = orphaned + [subject for _, subject in unstarted]
        if released:
            await db.execute(
                delete(ExecutionWorkerClaim).where(
                    ExecutionWorkerClaim.worker_id == worker_id,
                    ExecutionWorkerClaim.subject_id.in_(released),
                )
            )
            logger.warning(
                "Released work this executor claimed but never started",
                extra={"worker_id": str(worker_id), "ended": len(ended), "requeued": len(unstarted)},
            )
        await db.commit()
    if unstarted:
        from src.services.work_dispatch import wake_pool_workers

        await wake_pool_workers()
    await announce_ended_runs(ended, CLAIM_NOT_STARTED)
    return ended


async def announce_ended_runs(execution_ids: list[UUID], error: str = EXECUTOR_LOST) -> None:
    """Best-effort live update for runs ended after their executor was lost,
    and the result a sync caller is waiting for (it expires unread for the
    others), so no caller waits out its timeout."""
    from src.core.pubsub import publish_execution_update
    from src.core.redis_client import get_redis_client

    for execution_id in execution_ids:
        try:
            await publish_execution_update(execution_id, "Failed", {"error": error})
            await get_redis_client().push_result(
                execution_id=str(execution_id),
                status="Failed",
                error=error,
                error_type="ExecutorLost",
                duration_ms=0,
            )
        except Exception:
            logger.warning("Could not announce an ended run", exc_info=True)


__all__ = [
    "CLAIM_NOT_STARTED",
    "EXECUTOR_LOST",
    "LostRuns",
    "announce_ended_runs",
    "announce_lost_runs",
    "end_runs_of_lost_worker",
    "release_unstarted_claims",
]
