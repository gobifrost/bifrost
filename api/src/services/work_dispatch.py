"""Durable acceptance and dispatch of domain work (transactional outbox).

Acceptance inserts a ``WorkDispatch`` in the same transaction as the domain
record. After commit the accepting process wakes the executors, which claim
dispatches from PostgreSQL (the wake only saves them their poll interval).
An executor claims a dispatch by moving its subject from waiting to running
and deleting the dispatch in one transaction, so a duplicate or stale
delivery finds nothing to claim. The control plane's reconciler releases
work held by a lost executor and fails work no capacity claimed in time.

Started work is never replayed: once claimed, a run's recovery belongs to
its domain's terminal sweeps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.models.orm.work_dispatches import WorkDispatch

logger = logging.getLogger(__name__)

RECONCILE_BATCH = 100
# Dispatches are re-examined once they are this old (cancelled runs,
# dispatches held by a lost worker), and fail visibly if no worker capacity
# claimed them by the claim deadline.
POOL_RECHECK_AFTER = timedelta(minutes=2)
POOL_CLAIM_DEADLINE = timedelta(minutes=30)
# A worker of either form that has not heartbeated this long holds nothing.
WORKER_SILENT_AFTER = timedelta(minutes=2)


@dataclass(frozen=True)
class DispatchFamily:
    """How the reconciler reads, fails and reports one family's subjects.

    ``lock_waiting`` locks the subject (SKIP LOCKED) before the dispatch is
    locked, the same order as an executor's claim, and returns None when an
    executor holds it, True while it waits for an executor, False otherwise.
    """

    lock_waiting: Callable[[AsyncSession, UUID], Awaitable[bool | None]]
    fail: Callable[[AsyncSession, UUID, str], Awaitable[None]]
    report_failed: Callable[[UUID, dict[str, Any], str], Awaitable[None]]


def _families() -> dict[str, DispatchFamily]:
    from src.services.execution import agent_run_dispatch, summary_dispatch, workflow_dispatch

    return {
        workflow_dispatch.FAMILY: DispatchFamily(
            lock_waiting=workflow_dispatch.lock_waiting_run,
            fail=workflow_dispatch.fail_unclaimed_run,
            report_failed=workflow_dispatch.report_unclaimed_run,
        ),
        agent_run_dispatch.FAMILY: DispatchFamily(
            lock_waiting=agent_run_dispatch.lock_waiting_agent_run,
            fail=agent_run_dispatch.fail_unclaimed_agent_run,
            report_failed=agent_run_dispatch.report_unclaimed_agent_run,
        ),
        summary_dispatch.FAMILY: DispatchFamily(
            lock_waiting=summary_dispatch.lock_waiting_summary,
            fail=summary_dispatch.fail_unclaimed_summary,
            report_failed=summary_dispatch.report_unclaimed_summary,
        ),
    }


def stage_dispatch(
    db: AsyncSession,
    *,
    family: str,
    subject_id: UUID,
    handler: str,
    handler_version: int,
    message: dict[str, Any],
    payload: dict[str, Any],
    memory_request_bytes: int,
) -> WorkDispatch:
    """Add a dispatch to the caller's transaction; hand off after commit.

    ``memory_request_bytes`` is what the work is expected to add
    (src.services.memory_requests); executors claim it only when it fits.

    Executors claim it from PostgreSQL.
    """
    dispatch_id = uuid4()
    dispatch = WorkDispatch(
        id=dispatch_id,
        family=family,
        subject_id=subject_id,
        handler=handler,
        handler_version=handler_version,
        message={
            **message,
            "dispatch_id": str(dispatch_id),
            "handler": handler,
            "handler_version": handler_version,
        },
        payload=payload,
        memory_request_bytes=memory_request_bytes,
    )
    db.add(dispatch)
    return dispatch


async def hand_off() -> None:
    """Fast-path hand-off after commit.

    The dispatch is already claimable; the hint only saves executors their
    poll interval.
    """
    await wake_pool_workers()


POOL_WAKE_CHANNEL = "bifrost:pool:wake"


async def wake_pool_workers() -> None:
    """Best-effort nudge to pool workers; never authority over claims."""
    from src.core.cache.redis_client import get_shared_redis

    try:
        redis = await get_shared_redis()
        await redis.publish(POOL_WAKE_CHANNEL, "1")
    except Exception:
        logger.debug("Could not wake pool workers", exc_info=True)


async def take_dispatch_payload(db: AsyncSession, dispatch_id: UUID) -> dict[str, Any] | None:
    """Delete a dispatch in the claimer's transaction and return its payload."""
    return await db.scalar(
        delete(WorkDispatch)
        .where(WorkDispatch.id == dispatch_id)
        .returning(WorkDispatch.payload)
    )


async def _reconcile_one(
    families: dict[str, DispatchFamily],
    dispatch_id: UUID,
    family_name: str,
    subject_id: UUID,
) -> None:
    """Settle one candidate in its own short transaction."""
    family = families.get(family_name)
    if family is None:
        logger.error(
            "Dispatch has no registered family",
            extra={"dispatch_id": str(dispatch_id), "family": family_name},
        )
        return
    async with get_db_context() as db:
        waiting = await family.lock_waiting(db, subject_id)
        if waiting is None:
            return  # An executor is claiming it right now.
        row = await db.scalar(
            select(WorkDispatch)
            .where(WorkDispatch.id == dispatch_id)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return
        if not waiting:
            # The run stopped waiting (cancelled) before an executor claimed it.
            await db.delete(row)
            return
        failure = await _settle_pool_dispatch(db, family, row)
    if failure is not None:
        await family.report_failed(subject_id, *failure)


async def _settle_pool_dispatch(
    db: AsyncSession, family: DispatchFamily, row: WorkDispatch
) -> tuple[dict[str, Any], str] | None:
    """Release work held by a dead worker; fail work no capacity picked up."""
    from src.models.orm.execution_workers import ExecutionWorker

    if row.assigned_worker_id is not None:
        worker = (
            await db.execute(
                select(ExecutionWorker.state, ExecutionWorker.heartbeat_at).where(
                    ExecutionWorker.id == row.assigned_worker_id
                )
            )
        ).one_or_none()
        silent = worker is not None and (
            worker.heartbeat_at is None
            or worker.heartbeat_at <= datetime.now(timezone.utc) - WORKER_SILENT_AFTER
        )
        if worker is None or worker.state in ("lost", "retired") or silent:
            # The worker that took it is gone before claiming the run.
            row.assigned_worker_id = None
        return None
    largest = await _largest_budget(db)
    if largest is not None and row.memory_request_bytes > largest:
        reason = (
            f"This work needs {row.memory_request_bytes // 2**20} MiB of memory; "
            f"the largest executor can hold {largest // 2**20} MiB."
        )
        await family.fail(db, row.subject_id, reason)
        payload = row.payload
        await db.delete(row)
        return payload, reason
    if row.created_at <= datetime.now(timezone.utc) - POOL_CLAIM_DEADLINE:
        reason = "No worker capacity became available for this run."
        await family.fail(db, row.subject_id, reason)
        payload = row.payload
        await db.delete(row)
        return payload, reason
    return None


async def _largest_budget(db: AsyncSession) -> int | None:
    """The most memory any executor can hand to one piece of work: the
    largest live budget, or what the worker controller can launch. None
    while no executor has reported (an outage is not too-large work)."""
    from src.core.cache.redis_client import get_redis
    from src.models.orm.execution_workers import ExecutionWorker
    from src.services.memory_requests import LAUNCHABLE_KEY

    live = await db.scalar(
        select(func.max(ExecutionWorker.memory_budget_bytes)).where(
            ExecutionWorker.state.in_(("ready", "draining"))
        )
    )
    async with get_redis() as redis:
        launchable = await redis.get(LAUNCHABLE_KEY)
    sizes = [size for size in (live, int(launchable) if launchable else None) if size is not None]
    return max(sizes) if sizes else None


async def reconcile_work_dispatches(*, limit: int = RECONCILE_BATCH) -> None:
    """Settle work nobody holds or claimed.

    Safe on every control replica concurrently: each candidate is settled
    under SKIP LOCKED.
    """
    now = datetime.now(timezone.utc)
    async with get_db_context() as db:
        candidates = (
            await db.execute(
                select(WorkDispatch.id, WorkDispatch.family, WorkDispatch.subject_id)
                .where(WorkDispatch.created_at <= now - POOL_RECHECK_AFTER)
                .order_by(WorkDispatch.created_at.asc())
                .limit(limit)
            )
        ).all()
    families = _families()
    for dispatch_id, family_name, subject_id in candidates:
        await _reconcile_one(families, dispatch_id, family_name, subject_id)


__all__ = [
    "DispatchFamily",
    "POOL_CLAIM_DEADLINE",
    "POOL_WAKE_CHANNEL",
    "hand_off",
    "reconcile_work_dispatches",
    "stage_dispatch",
    "take_dispatch_payload",
]
