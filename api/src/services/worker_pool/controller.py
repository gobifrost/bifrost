"""Capacity controller for reusable workers (runs in the control plane).

One owner for worker capacity: every control replica runs this, and launch
decisions are serialized by a transaction advisory lock, so replicas never
over-launch. Each incarnation is a durable row first; its Job is created
suspended under a deterministic name, its UID persisted, and only then
started. A restarted controller adopts existing Jobs by name and UID.

Workers drain themselves when idle. The controller only marks a worker
``lost`` when it misses its start deadline or its heartbeat, or its Job
disappears; work it held is released by the dispatch reconciler, and runs it
had started follow their domain's terminal sweeps (never replayed).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, literal, select, text

from src.config import Settings, get_settings
from src.core.database import get_db_context
from src.jobs.platform.kubernetes_client import (
    KubernetesApiError,
    KubernetesConfigurationError,
    KubernetesJobClient,
)
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.work_dispatches import WorkDispatch
from src.services.platform_jobs import WAITING_FOR_MEMORY
from src.services.memory_requests import (
    JOB_BASELINE_ESTIMATE_BYTES,
    LAUNCHABLE_KEY,
    job_budget_bytes,
    sized_job_limit_bytes,
)
from src.services.worker_pool.claims import CAPACITY_LOCK
from src.services.worker_pool.manifest import (
    WorkerJobSpec,
    build_worker_job_manifest,
    resolve_worker_job_spec,
    worker_job_name,
)
from src.services.worker_pool.liveness import Reachability, silent_executor
from src.services.worker_pool.recovery import announce_ended_runs, end_runs_of_lost_worker

logger = logging.getLogger(__name__)

LIVE_STATES = ("reserved", "starting", "ready", "draining")
# How long a lost worker's Job is kept under watch for deletion.
LOST_JOB_CLEANUP = timedelta(hours=1)
RECONCILE_SECONDS = 2.0
# The reconciler treats a missing value as "no controller"; it outlives a
# few failed reconciles.
LAUNCHABLE_TTL_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerPoolController:
    def __init__(
        self, client: KubernetesJobClient, settings: Settings, spec: WorkerJobSpec
    ) -> None:
        self.client = client
        self.settings = settings
        self.spec = spec
        # Lost needs a steadily reachable database (worker_pool.liveness).
        self.reachability = Reachability()

    async def reconcile(self) -> None:
        # Before anything that can fail on the Kubernetes API, so the
        # reconciler never mistakes a struggling controller for none.
        async with get_db_context() as db:
            workers = (
                await db.execute(
                    select(ExecutionWorker).where(ExecutionWorker.state.in_(LIVE_STATES))
                )
            ).scalars().all()
            await _publish_launchable(self._limits(_job_baseline(workers, self.settings))[2])
        async with get_db_context() as db:
            ids = (
                await db.scalars(
                    select(ExecutionWorker.id).where(
                        ExecutionWorker.form == "job",
                        ExecutionWorker.kubernetes_namespace == self.client.namespace,
                        ExecutionWorker.state.in_(LIVE_STATES)
                        | (
                            (ExecutionWorker.state == "lost")
                            & (ExecutionWorker.ended_at >= _now() - LOST_JOB_CLEANUP)
                        ),
                    )
                )
            ).all()
        for worker_id in ids:
            await self.reconcile_worker(worker_id)
        for worker_id in await self.launch_for_demand():
            await self.reconcile_worker(worker_id)

    async def reconcile_worker(self, worker_id: UUID) -> None:
        async with get_db_context() as db:
            worker = await db.scalar(
                select(ExecutionWorker)
                .where(ExecutionWorker.id == worker_id)
                .with_for_update(skip_locked=True)
            )
            if worker is None or worker.kubernetes_job_name is None:
                return
            name = worker.kubernetes_job_name
            remote = await self.client.get_job(name)
            if remote is not None and worker.kubernetes_job_uid is not None:
                if remote["metadata"]["uid"] != worker.kubernetes_job_uid:
                    # Never adopt or delete a replacement that reused the name.
                    raise RuntimeError("Worker Job UID does not match its incarnation")
            now = _now()

            if worker.state == "lost":
                if remote is not None:
                    await self.client.delete_job(name, remote["metadata"]["uid"])
                return

            if worker.state == "reserved":
                if remote is None:
                    remote = await self.client.create_job(
                        build_worker_job_manifest(
                            worker.id,
                            self.settings,
                            self.spec,
                            memory_limit_bytes=worker.memory_limit_bytes,
                        )
                    )
                worker.kubernetes_job_uid = remote["metadata"]["uid"]
                worker.state = "starting"
                await db.commit()
                # The binding is durable; only now may the pod start.
                await self.client.start_job(name, remote["metadata"]["uid"])
                return

            reason = None
            if remote is None:
                reason = "job_missing"
            elif worker.state == "starting":
                if remote.get("spec", {}).get("suspend"):
                    await self.client.start_job(name, remote["metadata"]["uid"])
                elif now - worker.created_at >= timedelta(
                    seconds=self.settings.pool_start_timeout_seconds
                ):
                    reason = "start_timeout"
            elif self.reachability.steady() and await db.scalar(
                select(func.count())
                .select_from(ExecutionWorker)
                .where(ExecutionWorker.id == worker.id, silent_executor())
            ):
                reason = "heartbeat_lost"
            ended: list[UUID] = []
            if reason is not None:
                self._lose(worker, reason, now)
                ended = await end_runs_of_lost_worker(db, worker.id)
            await db.commit()
        await announce_ended_runs(ended)

    @staticmethod
    def _lose(worker: ExecutionWorker, reason: str, now: datetime) -> None:
        logger.warning(
            "Reusable worker lost",
            extra={"worker_id": str(worker.id), "reason": reason, "state": worker.state},
        )
        worker.state = "lost"
        worker.end_reason = reason
        worker.ended_at = now

    def _limits(self, baseline_bytes: int) -> tuple[int, int, int]:
        """Standard Job limit, its budget, and the largest budget launchable."""
        threshold = self.settings.memory_pressure_threshold
        standard_limit = self.settings.pool_worker_memory_limit_mib * 2**20
        largest_limit = max(standard_limit, self.settings.pool_worker_max_memory_mib * 2**20)
        return (
            standard_limit,
            job_budget_bytes(standard_limit, threshold, baseline_bytes),
            job_budget_bytes(largest_limit, threshold, baseline_bytes),
        )

    async def launch_for_demand(self) -> list[UUID]:
        """Reserve incarnations for waiting work, sized by memory, within ceilings.

        Waiting requests (dispatches, and local platform jobs waiting for
        executor memory) are placed oldest first into the free budget of
        executors that can claim (a Job that has not reported its budget
        counts as the one expected). What does not fit goes into new
        standard Jobs, or a Job sized for one request a standard Job cannot
        hold (reserved for it when it is a dispatch). The warm minimum is
        kept, and Jobs never exceed ``pool_max_workers``.
        """
        async with get_db_context() as db:
            locked = (
                await db.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": CAPACITY_LOCK},
                )
            ).scalar_one()
            if not locked:
                return []
            waiting = (
                await db.execute(
                    select(
                        WorkDispatch.id.label("id"),
                        WorkDispatch.memory_request_bytes.label("memory_request_bytes"),
                        WorkDispatch.created_at.label("created_at"),
                        literal(True).label("is_dispatch"),
                    )
                    .where(WorkDispatch.assigned_worker_id.is_(None))
                    .union_all(
                        select(
                            PlatformJob.id,
                            PlatformJob.memory_required_bytes,
                            PlatformJob.created_at,
                            literal(False),
                        ).where(
                            PlatformJob.status == "queued",
                            PlatformJob.execution_backend == "local",
                            PlatformJob.phase == WAITING_FOR_MEMORY,
                        )
                    )
                    .order_by(text("created_at"))
                )
            ).all()
            dispatch_ids = {row.id for row in waiting if row.is_dispatch}
            workers = (
                await db.execute(
                    select(ExecutionWorker).where(ExecutionWorker.state.in_(LIVE_STATES))
                )
            ).scalars().all()
            baseline = _job_baseline(workers, self.settings)
            standard_limit, standard_budget, largest_budget = self._limits(baseline)

            requests = {row.id: row.memory_request_bytes for row in waiting}
            free: list[int] = []
            covered: set[UUID] = set()
            for worker in workers:
                if worker.state == "draining" or worker.memory_pressured:
                    continue
                # A worker launched for one dispatch holds it until it claims it.
                held = 0
                if worker.reserved_for_dispatch_id in requests:
                    covered.add(worker.reserved_for_dispatch_id)
                    held = requests[worker.reserved_for_dispatch_id]
                # A Job admitted but still starting its pool has not measured
                # its budget yet; until it reports, count the one expected.
                if worker.state in ("reserved", "starting") or (
                    worker.form == "job" and worker.memory_budget_bytes is None
                ):
                    limit = worker.memory_limit_bytes or standard_limit
                    budget = job_budget_bytes(limit, self.settings.memory_pressure_threshold, baseline)
                    free.append(max(0, budget - held))
                elif worker.memory_budget_bytes is not None:
                    free.append(
                        max(0, worker.memory_budget_bytes - worker.memory_reserved_bytes - held)
                    )
            plan = _plan_launches(
                [(row.id, row.memory_request_bytes) for row in waiting if row.id not in covered],
                free,
                standard_budget=standard_budget,
                largest_budget=largest_budget,
            )
            jobs = sum(1 for worker in workers if worker.form == "job")
            # Only standard Jobs make up the warm minimum; a sized Job exists
            # for its large work and drains once idle.
            undrained = sum(
                1
                for worker in workers
                if worker.form == "job"
                and worker.resource_class == "standard"
                and worker.state != "draining"
            )
            warm = max(
                0, self.settings.pool_min_workers - undrained - plan.standard - len(plan.sized)
            )
            room = max(0, self.settings.pool_max_workers - jobs)
            launched: list[UUID] = []
            for dispatch_id, request in plan.sized:
                if len(launched) >= room:
                    break
                launched.append(
                    self._reserve(
                        db,
                        memory_limit_bytes=sized_job_limit_bytes(
                            request,
                            self.settings.memory_pressure_threshold,
                            baseline,
                            max(standard_limit, self.settings.pool_worker_max_memory_mib * 2**20),
                        ),
                        reserved_for=dispatch_id if dispatch_id in dispatch_ids else None,
                    )
                )
            for _ in range(plan.standard + warm):
                if len(launched) >= room:
                    break
                launched.append(self._reserve(db, memory_limit_bytes=None, reserved_for=None))
            await db.commit()
        if launched:
            logger.info(
                "Launching reusable workers",
                extra={
                    "count": len(launched),
                    "waiting": len(waiting),
                    "sized": len(plan.sized),
                    "free_mib": sum(free) // 2**20,
                },
            )
        return launched

    def _reserve(
        self, db: Any, *, memory_limit_bytes: int | None, reserved_for: UUID | None
    ) -> UUID:
        worker_id = uuid4()
        db.add(
            ExecutionWorker(
                id=worker_id,
                form="job",
                state="reserved",
                resource_class="standard" if memory_limit_bytes is None else "sized",
                slots=0,
                memory_limit_bytes=memory_limit_bytes,
                reserved_for_dispatch_id=reserved_for,
                kubernetes_namespace=self.client.namespace,
                kubernetes_job_name=worker_job_name(worker_id),
            )
        )
        return worker_id


@dataclass(frozen=True)
class LaunchPlan:
    standard: int
    sized: list[tuple[UUID, int]]


def _plan_launches(
    waiting: list[tuple[UUID, int]],
    free: list[int],
    *,
    standard_budget: int,
    largest_budget: int,
) -> LaunchPlan:
    """Place requests (oldest first) into free budgets, then into new Jobs.

    First fit: each request takes the first budget that holds it. A request
    no standard Job can hold gets its own sized Job; one larger than the
    largest launchable budget is left for the reconciler to fail.
    """
    bins = list(free)
    new_bins: list[int] = []
    sized: list[tuple[UUID, int]] = []
    for dispatch_id, request in waiting:
        placed = False
        for index, room in enumerate(bins):
            if room >= request:
                bins[index] -= request
                placed = True
                break
        if placed:
            continue
        if request > standard_budget:
            if request <= largest_budget:
                sized.append((dispatch_id, request))
            continue
        for index, room in enumerate(new_bins):
            if room >= request:
                new_bins[index] -= request
                placed = True
                break
        if not placed:
            new_bins.append(standard_budget - request)
    return LaunchPlan(standard=len(new_bins), sized=sized)


def _job_baseline(workers: Any, settings: Settings) -> int:
    """A Job worker's own memory, from the largest any Job reported (its
    limit at the threshold, less the budget it measured)."""
    standard_limit = settings.pool_worker_memory_limit_mib * 2**20
    reported = [
        int((worker.memory_limit_bytes or standard_limit) * settings.memory_pressure_threshold)
        - worker.memory_budget_bytes
        for worker in workers
        if worker.form == "job" and worker.memory_budget_bytes is not None
    ]
    return max(reported, default=JOB_BASELINE_ESTIMATE_BYTES)


async def _publish_launchable(largest_budget: int) -> None:
    """Tell the dispatch reconciler the largest budget this controller can launch."""
    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        await redis.set(LAUNCHABLE_KEY, str(largest_budget), ex=LAUNCHABLE_TTL_SECONDS)


async def worker_pool_loop(stop_event: asyncio.Event, settings: Any | None = None) -> None:
    settings = settings or get_settings()
    try:
        client = KubernetesJobClient(settings)
        spec = await resolve_worker_job_spec(client, settings)
    except (KubernetesConfigurationError, KubernetesApiError) as exc:
        # Pool work waits visibly (and fails at its claim deadline) instead of
        # taking down the process that hosts this loop.
        logger.error("Reusable worker controller disabled: %s", exc)
        await stop_event.wait()
        return
    controller = WorkerPoolController(client, settings, spec)
    try:
        while not stop_event.is_set():
            try:
                await controller.reconcile()
                controller.reachability.connected()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                controller.reachability.broken()
                logger.error(
                    "Reusable worker reconciliation failed (%s): %s",
                    type(exc).__name__,
                    exc,
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=RECONCILE_SECONDS)
    finally:
        await client.close()


__all__ = ["WorkerPoolController", "worker_pool_loop"]
