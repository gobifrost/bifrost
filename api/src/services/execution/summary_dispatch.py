"""Durable dispatch of agent-run summaries.

A summary is requested by marking the run's ``summary_status`` ``pending``
and staging a ``WorkDispatch`` in the same transaction. The worker claims it
under the run's row lock (run first, then dispatch: the reconciler's lock
order too), moving ``pending`` to ``generating`` and deleting the dispatch,
so a duplicate delivery finds nothing to do.

Live summaries and backfills are one family with two handlers, so a pool
executor gives each its own slot and a large backfill never delays live
summaries. Backfill membership is ``AgentRun.summary_backfill_job_id``: the
outcome is counted however the summary is delivered or recovered.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.models.orm.agent_runs import AgentRun
from src.models.orm.work_dispatches import WorkDispatch
from src.services.memory_requests import SUMMARY_REQUEST_BYTES
from src.services.work_dispatch import hand_off, stage_dispatch, take_dispatch_payload

logger = logging.getLogger(__name__)

FAMILY = "agent_summary"
HANDLER = "agent.summarize"
BACKFILL_HANDLER = "agent.summarize_backfill"
HANDLER_VERSION = 1


async def stage_summary(
    db: AsyncSession, run_id: UUID, *, backfill: bool = False
) -> WorkDispatch | None:
    """Add a summary dispatch to the caller's transaction.

    The caller marks the run ``pending`` in the same transaction. Returns
    None when the run already has a summary dispatch: that one will run. The
    insert never fails the caller's transaction (a run's completion commits
    with it).
    """
    dispatch = stage_dispatch(
        db,
        family=FAMILY,
        subject_id=run_id,
        handler=BACKFILL_HANDLER if backfill else HANDLER,
        handler_version=HANDLER_VERSION,
        message={"run_id": str(run_id)},
        payload={},
        memory_request_bytes=SUMMARY_REQUEST_BYTES,
    )
    db.expunge(dispatch)
    inserted = await db.scalar(
        pg_insert(WorkDispatch)
        .values(
            {
                column.key: getattr(dispatch, column.key)
                for column in WorkDispatch.__table__.columns
                if getattr(dispatch, column.key) is not None
            }
        )
        .on_conflict_do_nothing(index_elements=["family", "subject_id"])
        .returning(WorkDispatch.id)
    )
    return dispatch if inserted is not None else None


async def request_summary(db: AsyncSession, run: AgentRun) -> WorkDispatch | None:
    """Ask for a fresh summary of ``run`` (regenerate), in the caller's transaction."""
    run.summary_status = "pending"
    run.summary_error = None
    run.summary_backfill_job_id = None
    return await stage_summary(db, run.id)


async def stage_backfill_summaries(
    db: AsyncSession, run_ids: list[UUID]
) -> list[WorkDispatch]:
    """Stage a backfill's summaries; runs that already have one keep it."""
    staged = [await stage_summary(db, run_id, backfill=True) for run_id in run_ids]
    return [dispatch for dispatch in staged if dispatch is not None]


async def hand_off_summary(dispatch: WorkDispatch | None) -> None:
    """Fast-path hand-off after commit; the reconciler covers a failure."""
    if dispatch is None:
        return
    try:
        await hand_off()
    except Exception:
        logger.warning(
            "Summary hand-off failed; the reconciler will retry",
            extra={"run_id": log_safe(dispatch.subject_id)},
            exc_info=True,
        )


async def claim_summary(db: AsyncSession, run_id: UUID, dispatch_id: UUID) -> bool:
    """Claim a dispatched summary: ``pending`` → ``generating``.

    False when the run moved on (summarized, or no longer completed) or the
    dispatch was already taken by another delivery.
    """
    run = await db.scalar(
        select(AgentRun).where(AgentRun.id == run_id).with_for_update(of=AgentRun)
    )
    taken = await take_dispatch_payload(db, dispatch_id)
    if run is None or taken is None:
        return False
    if run.status != "completed" or run.summary_status != "pending":
        return False
    run.summary_status = "generating"
    run.summary_error = None
    return True


async def lock_waiting_summary(db: AsyncSession, run_id: UUID) -> bool | None:
    """Lock the run before its dispatch (the claim's order); None if busy."""
    row = (
        await db.execute(
            select(AgentRun.status, AgentRun.summary_status)
            .where(AgentRun.id == run_id)
            .with_for_update(skip_locked=True)
        )
    ).one_or_none()
    if row is not None:
        return row.status == "completed" and row.summary_status == "pending"
    exists = await db.scalar(select(AgentRun.id).where(AgentRun.id == run_id))
    return None if exists is not None else False


async def fail_unclaimed_summary(db: AsyncSession, run_id: UUID, reason: str) -> None:
    await db.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.summary_status == "pending")
        .values(summary_status="failed", summary_error=reason)
    )


async def report_unclaimed_summary(
    run_id: UUID, payload: dict[str, Any], reason: str
) -> None:
    """Count a summary nothing picked up against its backfill."""
    from src.core.database import get_session_factory
    from src.services.execution.backfill_tracker import record_backfill_outcome

    factory = get_session_factory()
    async with factory() as db:
        job_id = await db.scalar(
            select(AgentRun.summary_backfill_job_id).where(AgentRun.id == run_id)
        )
    if job_id is not None:
        await record_backfill_outcome(
            job_id=job_id, run_id=run_id, succeeded=False, session_factory=factory
        )


__all__ = [
    "BACKFILL_HANDLER",
    "FAMILY",
    "HANDLER",
    "HANDLER_VERSION",
    "claim_summary",
    "fail_unclaimed_summary",
    "hand_off_summary",
    "lock_waiting_summary",
    "report_unclaimed_summary",
    "request_summary",
    "stage_backfill_summaries",
    "stage_summary",
]
