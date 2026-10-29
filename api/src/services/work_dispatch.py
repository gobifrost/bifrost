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

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import delete, event, func, select
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
# An executor that ended this recently still counts as capacity for judging
# work too large: it is most likely restarting or being replaced.
RECENTLY_LIVE = timedelta(minutes=10)


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
    queued_at: datetime | None = None,
) -> WorkDispatch:
    """Add a dispatch to the caller's transaction; hand off after commit.

    ``memory_request_bytes`` is what the work is expected to add
    (src.services.memory_requests); executors claim it only when it fits.
    ``queued_at`` keeps the place in line of work handed back.

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
    if queued_at is not None:
        dispatch.created_at = queued_at
    db.add(dispatch)
    return dispatch


async def hand_off() -> None:
    """Fast-path hand-off after commit.

    The dispatch is already claimable; the hint only saves executors their
    poll interval.
    """
    await wake_pool_workers()


POOL_WAKE_CHANNEL = "bifrost:pool:wake"
WAKE_RECONNECT_SECONDS = 5.0


async def listen_for_pool_wake(redis_url: str, *events: asyncio.Event) -> None:
    """Set ``events`` on every pool wake-up until cancelled. Wake-ups are
    hints: a dropped connection reconnects, and listeners sweep meanwhile."""
    import redis.asyncio as aioredis

    while True:
        client = aioredis.from_url(redis_url)
        try:
            pubsub = client.pubsub()
            await pubsub.subscribe(POOL_WAKE_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    for event in events:
                        event.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Pool wake listener reconnecting", exc_info=True)
            await asyncio.sleep(WAKE_RECONNECT_SECONDS)
        finally:
            await client.aclose()


async def wait_for_next_pass(
    stop: asyncio.Event, wake: asyncio.Event, *, since: float, spacing: float, sweep: float
) -> None:
    """Wait until a loop's next pass: on a wake-up, but no sooner than
    ``spacing`` after the last pass began (``since``, monotonic), or after
    ``sweep`` without one. Clears ``wake``. Returns early on ``stop``."""
    stopped = asyncio.ensure_future(stop.wait())
    woken = asyncio.ensure_future(wake.wait())
    try:
        remaining = sweep - (time.monotonic() - since)
        await asyncio.wait({stopped, woken}, timeout=max(0.0, remaining), return_when=asyncio.FIRST_COMPLETED)
        if wake.is_set() and not stop.is_set():
            delay = spacing - (time.monotonic() - since)
            if delay > 0:
                await asyncio.wait({stopped}, timeout=delay)
    finally:
        stopped.cancel()
        woken.cancel()
        wake.clear()


_wakes: set[asyncio.Task[None]] = set()


def wake_pool_workers_after_commit(db: AsyncSession) -> None:
    """Wake idle executors once ``db`` commits: a hint sent before the commit
    would find nothing to claim (executors also sweep every 15 s)."""

    def publish(_session: Any) -> None:
        task = asyncio.get_running_loop().create_task(wake_pool_workers())
        _wakes.add(task)
        task.add_done_callback(_wakes.discard)

    event.listen(db.sync_session, "after_commit", publish, once=True)


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
    largest: int | None,
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
        held_by = row.assigned_worker_id
        failure = await _settle_pool_dispatch(db, family, row, largest)
        released = held_by is not None and row.assigned_worker_id is None
    if released:
        await wake_pool_workers()
    if failure is not None:
        await family.report_failed(subject_id, *failure)


async def _settle_pool_dispatch(
    db: AsyncSession, family: DispatchFamily, row: WorkDispatch, largest: int | None
) -> tuple[dict[str, Any], str] | None:
    """Release work held by a dead worker; fail work no executor can ever
    hold (``largest``: see ``largest_budget``) or no capacity picked up."""
    from src.models.orm.execution_workers import ExecutionWorker

    if row.assigned_worker_id is not None:
        state = await db.scalar(
            select(ExecutionWorker.state).where(ExecutionWorker.id == row.assigned_worker_id)
        )
        if state is None or state in ("lost", "retired"):
            # The worker that took it is gone before claiming the run.
            row.assigned_worker_id = None
        return None
    if largest is not None and row.memory_request_bytes > largest:
        from src.services.worker_pool.unfit import too_large_message

        reason = too_large_message(row.memory_request_bytes, largest)
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


async def largest_budget(db: AsyncSession, handler: str = "workflow.execute") -> int | None:
    """The most memory any executor can hand to one piece of work: the
    largest budget of an executor live now or within ``RECENTLY_LIVE`` (one
    restarting or rolling over counts), or what the worker controller can
    launch. Only executors supporting this handler count. A physical worker
    launch adds workflow and agent-run capacity, never the API's summary
    capacity. None while no compatible executor has reported (an outage is
    not too-large work).
    """
    from src.core.cache.redis_client import get_redis
    from src.models.orm.execution_workers import ExecutionWorker
    from src.services.memory_requests import LAUNCHABLE_KEY
    from src.services.worker_pool.claims import REMOTE_WORKER_HANDLER_NAMES

    live = await db.scalar(
        select(func.max(ExecutionWorker.memory_budget_bytes)).where(
            ExecutionWorker.supported_handlers.contains([handler]),
            (
                ExecutionWorker.state.in_(("ready", "draining"))
                | (ExecutionWorker.ended_at >= func.now() - RECENTLY_LIVE)
            ),
        )
    )
    launchable = None
    if handler in REMOTE_WORKER_HANDLER_NAMES:
        async with get_redis() as redis:
            launchable = await redis.get(LAUNCHABLE_KEY)
    sizes = [size for size in (live, int(launchable) if launchable else None) if size is not None]
    return max(sizes) if sizes else None


async def reconcile_work_dispatches(*, limit: int = RECONCILE_BATCH) -> None:
    """Settle work nobody holds or claimed.

    Safe on every control replica concurrently: each candidate is settled
    under SKIP LOCKED. Work larger than any executor can hold is settled
    at once, whatever its age, so its caller hears within seconds.
    """
    now = datetime.now(timezone.utc)
    async with get_db_context() as db:
        from src.services.worker_pool.claims import SUPPORTED_HANDLERS

        budgets = {
            handler: await largest_budget(db, handler)
            for handler, _version in SUPPORTED_HANDLERS
        }
        due = WorkDispatch.created_at <= now - POOL_RECHECK_AFTER
        for handler, largest in budgets.items():
            if largest is not None:
                due = due | (
                    WorkDispatch.assigned_worker_id.is_(None)
                    & (WorkDispatch.handler == handler)
                    & (WorkDispatch.memory_request_bytes > largest)
                )
        candidates = (
            await db.execute(
                select(
                    WorkDispatch.id, WorkDispatch.family,
                    WorkDispatch.subject_id, WorkDispatch.handler,
                )
                .where(due)
                .order_by(WorkDispatch.created_at.asc())
                .limit(limit)
            )
        ).all()
    families = _families()
    for dispatch_id, family_name, subject_id, handler in candidates:
        await _reconcile_one(families, dispatch_id, family_name, subject_id, budgets.get(handler))


__all__ = [
    "DispatchFamily",
    "listen_for_pool_wake",
    "wake_pool_workers_after_commit",
    "wait_for_next_pass",
    "POOL_CLAIM_DEADLINE",
    "POOL_WAKE_CHANNEL",
    "hand_off",
    "reconcile_work_dispatches",
    "stage_dispatch",
    "take_dispatch_payload",
]
