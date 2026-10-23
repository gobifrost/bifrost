"""The reusable worker's side of the pool protocol (all fenced in PostgreSQL)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, text, tuple_, update

from src.core.database import get_db_context
from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
from src.models.orm.work_dispatches import WorkDispatch

# Shared with the capacity controller: launches and idle drains serialize.
CAPACITY_LOCK = "bifrost:worker-pool:capacity"
# Work that has waited this long without fitting anywhere stops executors
# that could hold it from claiming newer work, so it is never starved.
HEAD_OF_LINE_WAIT = timedelta(seconds=30)

# Handlers this worker build can run, by version. A dispatch for any other
# (handler, version) stays for a compatible worker.
SUPPORTED_HANDLERS: tuple[tuple[str, int], ...] = (
    ("workflow.execute", 1),
    ("agent.run", 1),
    ("agent.summarize", 1),
    ("agent.summarize_backfill", 1),
)


@dataclass(frozen=True)
class PoolClaim:
    dispatch_id: UUID
    handler: str
    message: dict[str, Any]
    subject_id: UUID
    memory_request_bytes: int
    # When the work was accepted: work handed back keeps its place in line.
    queued_at: datetime
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
                heartbeat_at=func.now(),
                last_claim_at=now,
            )
            .returning(ExecutionWorker.slots)
        )


async def register_static_worker(
    *, slots: int, hostname: str, memory_budget_bytes: int
) -> UUID:
    """A long-running executor joins the pool directly as ``ready``.

    ``memory_budget_bytes`` is measured before its pool starts; the first
    heartbeat replaces it with the budget measured once ready.
    """
    now = _now()
    worker = ExecutionWorker(
        form="static",
        state="ready",
        resource_class="standard",
        slots=slots,
        memory_budget_bytes=memory_budget_bytes,
        hostname=hostname,
        ready_at=now,
        heartbeat_at=func.now(),
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
    *,
    fits_bytes: int,
    budget_bytes: int,
) -> PoolClaim | None:
    """Assign the oldest unassigned dispatch this worker can take now.

    ``handlers`` narrows the claim to the (handler, version) pairs with free
    capacity on this worker; it is always a subset of what the build supports.
    Only work whose memory request fits ``fits_bytes`` (the free budget) is
    taken, except that a worker launched for one dispatch takes it first
    whatever it measured: it exists for that work, and the real-memory gate
    and the dangerous-memory stop still apply. When older work
    that fits ``budget_bytes`` (but not the free budget) has waited past
    HEAD_OF_LINE_WAIT, nothing newer is taken until it fits.
    """
    pairs = [pair for pair in handlers if pair in SUPPORTED_HANDLERS]
    unassigned = (
        WorkDispatch.assigned_worker_id.is_(None),
        tuple_(WorkDispatch.handler, WorkDispatch.handler_version).in_(pairs),
    )
    reserved_for = (
        select(ExecutionWorker.reserved_for_dispatch_id)
        .where(ExecutionWorker.id == incarnation_id)
        .scalar_subquery()
    )
    candidate = (
        select(WorkDispatch.id, WorkDispatch.created_at)
        .where(
            *unassigned,
            (WorkDispatch.memory_request_bytes <= fits_bytes) | (WorkDispatch.id == reserved_for),
        )
        .order_by(
            (WorkDispatch.id == reserved_for).is_(True).desc(),
            WorkDispatch.created_at.asc(),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    ready = exists().where(
        ExecutionWorker.id == incarnation_id, ExecutionWorker.state == "ready"
    )
    async with get_db_context() as db:
        picked = (await db.execute(candidate)).one_or_none()
        if picked is None:
            return None
        blocked = await db.scalar(
            select(
                exists().where(
                    *unassigned,
                    WorkDispatch.created_at < picked.created_at,
                    WorkDispatch.created_at < _now() - HEAD_OF_LINE_WAIT,
                    WorkDispatch.memory_request_bytes > fits_bytes,
                    WorkDispatch.memory_request_bytes <= budget_bytes,
                )
            )
        )
        if blocked:
            return None
        row = (
            await db.execute(
                update(WorkDispatch)
                .where(WorkDispatch.id == picked.id, ready)
                .values(assigned_worker_id=incarnation_id)
                .returning(
                    WorkDispatch.id.label("dispatch_id"),
                    WorkDispatch.handler.label("handler"),
                    WorkDispatch.message.label("message"),
                    WorkDispatch.family.label("dispatch_family"),
                    WorkDispatch.subject_id.label("subject"),
                    WorkDispatch.memory_request_bytes.label("memory_request_bytes"),
                    WorkDispatch.created_at.label("queued_at"),
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
            .values(last_claim_at=_now(), reserved_for_dispatch_id=None)
        )
    return PoolClaim(
        dispatch_id=row.dispatch_id,
        handler=row.handler,
        message=row.message,
        subject_id=row.subject,
        memory_request_bytes=row.memory_request_bytes,
        queued_at=row.queued_at,
        claimed=dict(claimed) if claimed is not None else None,
    )


async def heartbeat_worker(
    incarnation_id: UUID,
    *,
    active_runs: int,
    runs_handled: int,
    package_generation: int,
    memory_pressured: bool,
    memory_budget_bytes: int | None,
    memory_reserved_bytes: int,
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
                # The database's clock: liveness is judged against it.
                heartbeat_at=func.now(),
                active_runs=active_runs,
                runs_handled=runs_handled,
                package_generation=package_generation,
                memory_pressured=memory_pressured,
                memory_budget_bytes=memory_budget_bytes,
                memory_reserved_bytes=memory_reserved_bytes,
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
        # A sized Job always drains once idle: only standard Jobs make up the
        # warm minimum.
        sized = await db.scalar(
            select(ExecutionWorker.resource_class == "sized").where(
                ExecutionWorker.id == incarnation_id
            )
        )
        ready_jobs = await db.scalar(
            select(func.count(ExecutionWorker.id)).where(
                ExecutionWorker.form == "job",
                ExecutionWorker.resource_class == "standard",
                ExecutionWorker.state == "ready",
            )
        ) or 0
        if not sized and ready_jobs <= keep_ready:
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
                memory_reserved_bytes=0,
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
