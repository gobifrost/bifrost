"""The reusable worker's side of the pool protocol (all fenced in PostgreSQL)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, text, tuple_, update

from src.core.database import get_db_context
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.models.orm.work_dispatches import WorkDispatch

# Shared with the capacity controller: launches and idle drains serialize.
CAPACITY_LOCK = "bifrost:worker-pool:capacity"

# Handlers this worker build can run, by version. A dispatch for any other
# (handler, version) stays for a compatible worker.
SUPPORTED_HANDLERS: tuple[tuple[str, int], ...] = (
    ("workflow.execute", 1),
    ("agent.run", 1),
)


@dataclass(frozen=True)
class PoolClaim:
    dispatch_id: UUID
    handler: str
    message: dict[str, Any]
    # A workflow run is claimed (PENDING -> RUNNING) in the same transaction
    # as its dispatch; this is its context. None when the consumer still has
    # to claim (agent runs) or when the run had already moved on.
    claimed: dict[str, Any] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def admit_worker(incarnation_id: UUID, *, job_uid: str, pod_uid: str) -> int | None:
    """Bind this pod to its incarnation once; return its slots, or None.

    Only the first pod of the UID-bound Job can admit: a second start of the
    same Job, or a pod of another Job, is refused.
    """
    now = _now()
    async with get_db_context() as db:
        return await db.scalar(
            update(ExecutionWorker)
            .where(
                ExecutionWorker.id == incarnation_id,
                ExecutionWorker.state == "starting",
                ExecutionWorker.kubernetes_job_uid == job_uid,
                ExecutionWorker.kubernetes_pod_uid.is_(None),
            )
            .values(
                state="ready",
                kubernetes_pod_uid=pod_uid,
                ready_at=now,
                heartbeat_at=now,
                last_claim_at=now,
            )
            .returning(ExecutionWorker.slots)
        )


async def register_static_worker(*, slots: int, hostname: str) -> UUID:
    """A long-running executor joins the pool directly as ``ready``."""
    now = _now()
    worker = ExecutionWorker(
        form="static",
        state="ready",
        resource_class="standard",
        slots=slots,
        hostname=hostname,
        ready_at=now,
        heartbeat_at=now,
        last_claim_at=now,
    )
    async with get_db_context() as db:
        db.add(worker)
        await db.flush()
        worker_id = worker.id
    return worker_id


async def claim_next(
    incarnation_id: UUID,
    handlers: tuple[tuple[str, int], ...] = SUPPORTED_HANDLERS,
) -> PoolClaim | None:
    """Assign the oldest unassigned pool dispatch this worker can take now.

    ``handlers`` narrows the claim to the (handler, version) pairs with free
    capacity on this worker; it is always a subset of what the build supports.
    """
    candidate = (
        select(WorkDispatch.id)
        .where(
            WorkDispatch.placement == "pool",
            WorkDispatch.assigned_worker_id.is_(None),
            tuple_(WorkDispatch.handler, WorkDispatch.handler_version).in_(
                [pair for pair in handlers if pair in SUPPORTED_HANDLERS]
            ),
        )
        .order_by(WorkDispatch.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    ready = exists().where(
        ExecutionWorker.id == incarnation_id, ExecutionWorker.state == "ready"
    )
    async with get_db_context() as db:
        row = (
            await db.execute(
                update(WorkDispatch)
                .where(WorkDispatch.id == candidate, ready)
                .values(assigned_worker_id=incarnation_id)
                .returning(
                    WorkDispatch.id.label("dispatch_id"),
                    WorkDispatch.handler.label("handler"),
                    WorkDispatch.message.label("message"),
                    WorkDispatch.family.label("dispatch_family"),
                    WorkDispatch.subject_id.label("subject"),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        db.add(
            ExecutionWorkerClaim(
                worker_id=incarnation_id,
                family=row.dispatch_family,
                subject_id=row.subject,
            )
        )
        claimed = None
        if row.handler == "workflow.execute":
            from src.services.execution.workflow_dispatch import claim_workflow_dispatch

            # One transaction for assignment and the run's own claim. A run
            # that moved on (cancelled) leaves claimed None: the consumer then
            # finds nothing to claim and discards it, waking a sync caller.
            claimed = await claim_workflow_dispatch(
                db, dispatch_id=row.dispatch_id, execution_id=row.subject
            )
        await db.execute(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == incarnation_id)
            .values(last_claim_at=_now())
        )
    return PoolClaim(
        dispatch_id=row.dispatch_id,
        handler=row.handler,
        message=row.message,
        claimed=dict(claimed) if claimed is not None else None,
    )


async def heartbeat_worker(
    incarnation_id: UUID, *, active_runs: int, runs_handled: int
) -> str | None:
    """Renew liveness; return the state the controller sees (None if gone)."""
    async with get_db_context() as db:
        return await db.scalar(
            update(ExecutionWorker)
            .where(
                ExecutionWorker.id == incarnation_id,
                ExecutionWorker.state.in_(("ready", "draining")),
            )
            .values(
                heartbeat_at=_now(),
                active_runs=active_runs,
                runs_handled=runs_handled,
            )
            .returning(ExecutionWorker.state)
        )


async def begin_drain(incarnation_id: UUID, reason: str) -> bool:
    """``ready → draining``; after this the worker can never claim again."""
    async with get_db_context() as db:
        drained = await db.scalar(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == incarnation_id, ExecutionWorker.state == "ready")
            .values(state="draining", end_reason=reason)
            .returning(ExecutionWorker.id)
        )
    return drained is not None


async def begin_idle_drain(incarnation_id: UUID, *, keep_ready: int) -> bool:
    """Drain an idle job worker unless it is needed for the warm minimum.

    Serialized with launches by the capacity lock, so idle workers never
    collectively drop below ``keep_ready``.
    """
    async with get_db_context() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": CAPACITY_LOCK},
        )
        ready_jobs = await db.scalar(
            select(func.count(ExecutionWorker.id)).where(
                ExecutionWorker.form == "job", ExecutionWorker.state == "ready"
            )
        ) or 0
        if ready_jobs <= keep_ready:
            return False
        drained = await db.scalar(
            update(ExecutionWorker)
            .where(ExecutionWorker.id == incarnation_id, ExecutionWorker.state == "ready")
            .values(state="draining", end_reason="idle")
            .returning(ExecutionWorker.id)
        )
    return drained is not None


async def retire_worker(incarnation_id: UUID, *, runs_handled: int) -> None:
    async with get_db_context() as db:
        await db.execute(
            update(ExecutionWorker)
            .where(
                ExecutionWorker.id == incarnation_id,
                ExecutionWorker.state.in_(("ready", "draining")),
            )
            .values(
                state="retired",
                active_runs=0,
                runs_handled=runs_handled,
                ended_at=_now(),
            )
        )


__all__ = [
    "PoolClaim",
    "SUPPORTED_HANDLERS",
    "admit_worker",
    "register_static_worker",
    "begin_drain",
    "begin_idle_drain",
    "claim_next",
    "heartbeat_worker",
    "retire_worker",
]
