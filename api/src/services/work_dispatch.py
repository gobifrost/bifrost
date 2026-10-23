"""Durable acceptance and dispatch of domain work (transactional outbox).

Acceptance inserts a ``WorkDispatch`` in the same transaction as the domain
record. After commit the accepting process publishes it on the fast path; the
control plane's reconciler publishes any dispatch whose hand-off was lost.
An executor claims a dispatch by moving its subject from waiting to running
and deleting the dispatch in one transaction, so a duplicate or stale
delivery finds nothing to claim.

Started work is never replayed: once claimed, a run's recovery belongs to
its domain's terminal sweeps.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context, get_dispatch_db_context
from src.jobs.rabbitmq import publish_message
from src.models.orm.work_dispatches import WorkDispatch

logger = logging.getLogger(__name__)

# A dispatch still unpublished this long after it was staged lost its fast
# path (the accepting process died or the broker refused it). Long enough
# that a large promoter batch handing off sequentially is never raced.
PENDING_GRACE = timedelta(seconds=60)
# A published dispatch unclaimed this long may have been lost by the broker.
# A busy queue legitimately holds messages, and a duplicate publish costs
# capacity (the claim discards it), so redelivery is rare and bounded.
REDELIVERY_WINDOW = timedelta(minutes=30)
# Deliveries before an unclaimed run fails visibly (about 90 minutes,
# longer than the hour a Redis-only run used to survive).
MAX_PUBLISHES = 3
RECONCILE_BATCH = 100


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
    from src.services.execution import workflow_dispatch

    return {
        workflow_dispatch.FAMILY: DispatchFamily(
            lock_waiting=workflow_dispatch.lock_waiting_run,
            fail=workflow_dispatch.fail_unclaimed_run,
            report_failed=workflow_dispatch.report_unclaimed_run,
        ),
    }


def stage_dispatch(
    db: AsyncSession,
    *,
    family: str,
    subject_id: UUID,
    handler: str,
    handler_version: int,
    queue: str,
    message: dict[str, Any],
    payload: dict[str, Any],
) -> WorkDispatch:
    """Add a dispatch to the caller's transaction; publish after commit."""
    dispatch_id = uuid4()
    dispatch = WorkDispatch(
        id=dispatch_id,
        family=family,
        subject_id=subject_id,
        handler=handler,
        handler_version=handler_version,
        queue=queue,
        message={
            **message,
            "dispatch_id": str(dispatch_id),
            "handler": handler,
            "handler_version": handler_version,
        },
        payload=payload,
        status="pending",
        publish_count=0,
    )
    db.add(dispatch)
    return dispatch


# Publish records in flight; held so they are not garbage-collected.
_publish_records: set[asyncio.Task[None]] = set()


async def _record_published(dispatch_id: UUID) -> None:
    try:
        async with get_dispatch_db_context() as db:
            await db.execute(
                update(WorkDispatch)
                .where(WorkDispatch.id == dispatch_id, WorkDispatch.status == "pending")
                .values(
                    status="published",
                    publish_count=WorkDispatch.publish_count + 1,
                    last_published_at=datetime.now(timezone.utc),
                )
            )
    except Exception:
        # At worst the reconciler publishes a duplicate after PENDING_GRACE,
        # which the claim discards.
        logger.warning(
            "Could not record dispatch publish",
            extra={"dispatch_id": str(dispatch_id)},
            exc_info=True,
        )


async def publish_dispatch(dispatch_id: UUID, queue: str, message: dict[str, Any]) -> bool:
    """Fast-path hand-off after commit. A failure leaves it to the reconciler.

    The publish is recorded in the background: callers (often a request that
    then waits for the run's result) do not wait for that write.
    """
    try:
        await publish_message(queue, message)
    except Exception:
        logger.warning(
            "Dispatch publish failed; the reconciler will retry",
            extra={"dispatch_id": str(dispatch_id), "queue": queue},
            exc_info=True,
        )
        return False
    task = asyncio.create_task(_record_published(dispatch_id))
    _publish_records.add(task)
    task.add_done_callback(_publish_records.discard)
    return True


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
) -> bool:
    """Settle one candidate in its own short transaction; True if published."""
    family = families.get(family_name)
    if family is None:
        logger.error(
            "Dispatch has no registered family",
            extra={"dispatch_id": str(dispatch_id), "family": family_name},
        )
        return False
    failure: tuple[dict[str, Any], str] | None = None
    hand_off: tuple[str, dict[str, Any], int] | None = None
    async with get_db_context() as db:
        waiting = await family.lock_waiting(db, subject_id)
        if waiting is None:
            return False  # An executor is claiming it right now.
        row = await db.scalar(
            select(WorkDispatch)
            .where(WorkDispatch.id == dispatch_id)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        if not waiting:
            # Cancelled, or claimed by an executor that predates dispatches.
            await db.delete(row)
            return False
        if row.publish_count >= MAX_PUBLISHES:
            reason = f"No executor claimed this run after {row.publish_count} deliveries."
            await family.fail(db, subject_id, reason)
            failure = (row.payload, reason)
            await db.delete(row)
        else:
            row.status = "published"
            row.publish_count += 1
            row.last_published_at = datetime.now(timezone.utc)
            hand_off = (row.queue, row.message, row.publish_count)
    if failure is not None:
        await family.report_failed(subject_id, *failure)
        return False
    if hand_off is None:
        return False
    queue, message, attempt = hand_off
    # Publish outside the transaction: no row lock is held across the broker.
    try:
        await publish_message(queue, message)
    except Exception:
        logger.warning(
            "Dispatch republish failed", extra={"dispatch_id": str(dispatch_id)}, exc_info=True
        )
        async with get_db_context() as db:
            await db.execute(
                update(WorkDispatch)
                .where(WorkDispatch.id == dispatch_id, WorkDispatch.publish_count == attempt)
                .values(status="pending", publish_count=attempt - 1)
            )
        return False
    return True


async def reconcile_work_dispatches(*, limit: int = RECONCILE_BATCH) -> int:
    """Publish lost hand-offs; drop dispatches whose subject moved on.

    Returns the number of dispatches published. Safe on every control
    replica concurrently: each candidate is settled under SKIP LOCKED.
    """
    now = datetime.now(timezone.utc)
    async with get_db_context() as db:
        candidates = (
            await db.execute(
                select(WorkDispatch.id, WorkDispatch.family, WorkDispatch.subject_id)
                .where(
                    or_(
                        (WorkDispatch.status == "pending")
                        & (WorkDispatch.created_at <= now - PENDING_GRACE),
                        (WorkDispatch.status == "published")
                        & (WorkDispatch.last_published_at <= now - REDELIVERY_WINDOW),
                    )
                )
                .order_by(WorkDispatch.created_at.asc())
                .limit(limit)
            )
        ).all()
    families = _families()
    published = 0
    for dispatch_id, family_name, subject_id in candidates:
        if await _reconcile_one(families, dispatch_id, family_name, subject_id):
            published += 1
    if published:
        logger.info("Reconciled work dispatches", extra={"published": published})
    return published


__all__ = [
    "DispatchFamily",
    "MAX_PUBLISHES",
    "PENDING_GRACE",
    "REDELIVERY_WINDOW",
    "publish_dispatch",
    "reconcile_work_dispatches",
    "stage_dispatch",
    "take_dispatch_payload",
]
