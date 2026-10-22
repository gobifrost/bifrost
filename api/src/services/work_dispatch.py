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

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.jobs.rabbitmq import publish_message
from src.models.orm.work_dispatches import WorkDispatch

logger = logging.getLogger(__name__)

# A dispatch still unpublished this long after commit lost its fast path.
PENDING_GRACE = timedelta(seconds=5)
# A published dispatch unclaimed this long may have been lost by the broker.
# Generous, because a busy queue legitimately holds messages for minutes;
# a duplicate publish is harmless (the claim fences it) but costs capacity.
REDELIVERY_WINDOW = timedelta(minutes=10)
MAX_PUBLISHES = 5
RECONCILE_BATCH = 100


@dataclass(frozen=True)
class DispatchFamily:
    """How the reconciler reads and fails one family's subjects."""

    is_waiting: Callable[[AsyncSession, UUID], Awaitable[bool]]
    fail: Callable[[AsyncSession, UUID, str], Awaitable[None]]


def _families() -> dict[str, DispatchFamily]:
    from src.services.execution import workflow_dispatch

    return {
        workflow_dispatch.FAMILY: DispatchFamily(
            is_waiting=workflow_dispatch.run_is_waiting,
            fail=workflow_dispatch.fail_unclaimed_run,
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


async def publish_dispatch(dispatch_id: UUID, queue: str, message: dict[str, Any]) -> bool:
    """Fast-path hand-off after commit. A failure leaves it to the reconciler."""
    try:
        await publish_message(queue, message)
    except Exception:
        logger.warning(
            "Dispatch publish failed; the reconciler will retry",
            extra={"dispatch_id": str(dispatch_id), "queue": queue},
            exc_info=True,
        )
        return False
    try:
        async with get_db_context() as db:
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
        # The message is with the broker; at worst the reconciler publishes a
        # duplicate, which the claim discards.
        logger.warning(
            "Could not record dispatch publish",
            extra={"dispatch_id": str(dispatch_id)},
            exc_info=True,
        )
    return True


async def take_dispatch_payload(db: AsyncSession, dispatch_id: UUID) -> dict[str, Any] | None:
    """Delete a dispatch in the claimer's transaction and return its payload."""
    return await db.scalar(
        delete(WorkDispatch)
        .where(WorkDispatch.id == dispatch_id)
        .returning(WorkDispatch.payload)
    )


async def reconcile_work_dispatches(*, limit: int = RECONCILE_BATCH) -> int:
    """Publish lost hand-offs; drop dispatches whose subject moved on.

    Returns the number of dispatches published. Safe on every control
    replica concurrently: rows are locked with SKIP LOCKED.
    """
    now = datetime.now(timezone.utc)
    published = 0
    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(WorkDispatch)
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
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        families = _families()
        for row in rows:
            family = families.get(row.family)
            if family is None:
                logger.error(
                    "Dispatch has no registered family",
                    extra={"dispatch_id": str(row.id), "family": row.family},
                )
                continue
            if not await family.is_waiting(db, row.subject_id):
                # Cancelled, or claimed by an executor that predates dispatches.
                await db.delete(row)
                continue
            if row.publish_count >= MAX_PUBLISHES:
                await family.fail(
                    db,
                    row.subject_id,
                    f"No executor claimed this run after {row.publish_count} deliveries.",
                )
                await db.delete(row)
                continue
            try:
                await publish_message(row.queue, row.message)
            except Exception:
                logger.warning(
                    "Dispatch republish failed",
                    extra={"dispatch_id": str(row.id)},
                    exc_info=True,
                )
                continue
            row.status = "published"
            row.publish_count += 1
            row.last_published_at = now
            published += 1
        await db.commit()
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
