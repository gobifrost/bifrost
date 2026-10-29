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

After an upgrade the controller replaces Jobs running a build no live
controller runs, launched by a controller that is gone (``LiveControllers``):
the ready warm minimum remains available while outdated Jobs drain (never
deleted), so their runs finish. Replacements launch after normal capacity
becomes available.

Scaling (Warm workers, Max workers, Worker memory, Largest worker) is read
from Settings -> Kubernetes at the start of every pass, so a change applies
without a restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, literal, select, text, update

from shared.version import get_version
from src.config import Settings, get_settings
from src.core.database import get_db_context
from src.jobs.platform.kubernetes_client import (
    KubernetesConfigurationError,
    KubernetesJobClient,
    oom_killed,
)
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.work_dispatches import WorkDispatch
from src.services.kubernetes_execution import KubernetesPolicy, load_kubernetes_policy
from src.services.platform_jobs import WAITING_FOR_MEMORY
from src.services.memory_requests import LAUNCHABLE_KEY
from src.services.work_dispatch import listen_for_pool_wake, wait_for_next_pass
from src.services.worker_pool.claims import CAPACITY_LOCK, REMOTE_WORKER_HANDLER_NAMES
from src.services.worker_pool.manifest import (
    SAFE_TO_EVICT,
    WorkerJobSpec,
    build_worker_job_manifest,
    resolve_job_spec_when_ready,
    worker_job_name,
)
from src.services.worker_pool.liveness import (
    HEARTBEAT_LOST_AFTER,
    Reachability,
    silent_executor,
)
from src.services.worker_pool.recovery import (
    LostRuns,
    announce_lost_runs,
    end_runs_of_lost_worker,
)
from src.services.worker_pool.sizing import LIVE_STATES, JobSizing, job_baseline, launch_limit

logger = logging.getLogger(__name__)

# How long a lost worker's Job is kept under watch for deletion.
LOST_JOB_CLEANUP = timedelta(hours=1)
# The controller reconciles on a pool wake-up (new work may need a worker), at
# most every RECONCILE_SECONDS, and sweeps every SWEEP_SECONDS without one:
# often enough for worker-loss detection and idle-worker eviction marks.
RECONCILE_SECONDS = 2.0
SWEEP_SECONDS = 5.0
# A ready worker holding no work this long may be evicted by the cluster
# autoscaler to empty a node; it drains on SIGTERM and is replaced.
EVICTABLE_AFTER_IDLE_SECONDS = 10.0
# The reconciler treats a missing value as "no controller"; it outlives a
# few failed reconciles.
LAUNCHABLE_TTL_SECONDS = 300
# Every controller instance's build, by instance id, with when it last wrote.
CONTROLLERS_KEY = "bifrost:worker-pool:controllers"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LiveControllers:
    """Controller instances that wrote within ``HEARTBEAT_LOST_AFTER``."""

    ids: frozenset[UUID]
    versions: frozenset[str]

    def outdated(self, worker: ExecutionWorker) -> bool:
        """An admitted Job whose build no live controller runs and whose
        launcher is gone. A Job launched by a live controller is never
        outdated, so a replacement can never be replaced in turn; a Job not
        yet admitted has not reported its build."""
        return (
            worker.form == "job"
            and worker.state in ("ready", "draining")
            and worker.version not in self.versions
            and worker.launched_by not in self.ids
        )


class WorkerPoolController:
    def __init__(
        self, client: KubernetesJobClient, settings: Settings, spec: WorkerJobSpec
    ) -> None:
        self.client = client
        self.settings = settings
        self.spec = spec
        # Lost needs a steadily reachable database (worker_pool.liveness).
        self.reachability = Reachability()
        # This instance, as the launcher recorded on the Jobs it creates.
        self.id = uuid4()
        self.version = get_version()
        self.live = LiveControllers(ids=frozenset({self.id}), versions=frozenset({self.version}))
        # Draining workers with active runs, by when this instance first saw them.
        self._draining_since: dict[UUID, float] = {}
        self._warned_long_drains: set[UUID] = set()
        # Ready workers holding no work, by when this instance first saw them idle.
        self._idle_since: dict[UUID, float] = {}
        # The safe-to-evict value this instance last applied to each worker's
        # pod. Unknown until applied once, so a restarted controller corrects
        # a mark left by its predecessor.
        self._evictable: dict[UUID, bool] = {}

    async def reconcile(self) -> None:
        # Before anything that can fail on the Kubernetes API, so the
        # reconciler never mistakes a struggling controller for none.
        async with get_db_context() as db:
            workers = (
                await db.execute(
                    select(ExecutionWorker).where(ExecutionWorker.state.in_(LIVE_STATES))
                )
            ).scalars().all()
            policy = await load_kubernetes_policy(db)
            await _publish_launchable(self._sizing(policy, workers).largest_budget_bytes)
        self.live = await _publish_live(self.id, self.version)
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
        for worker_id in set(self._draining_since) - set(ids):
            del self._draining_since[worker_id]
        for worker_id in set(self._evictable) - set(ids):
            self._evictable.pop(worker_id, None)
            self._idle_since.pop(worker_id, None)
        for worker_id in ids:
            await self._reconcile_one(worker_id, policy)
        for worker_id in await self.launch_for_demand(policy):
            await self._reconcile_one(worker_id, policy)
        await self.replace_outdated(policy)

    async def _reconcile_one(self, worker_id: UUID, policy: KubernetesPolicy) -> None:
        """One worker's failure (a slow Kubernetes API, a replaced Job) must
        not stop the others from being reconciled."""
        try:
            await self.reconcile_worker(worker_id, policy)
        except Exception as exc:
            logger.error(
                "Reusable worker reconciliation failed (%s): %s",
                type(exc).__name__,
                exc,
                extra={"worker_id": str(worker_id)},
            )

    async def reconcile_worker(self, worker_id: UUID, policy: KubernetesPolicy) -> None:
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
                            memory_limit_bytes=launch_limit(
                                worker, policy.worker_memory_mib * 2**20
                            ),
                            sized=worker.resource_class == "sized",
                        )
                    )
                    # Whoever creates the Job launches it, whichever replica
                    # reserved the row. A Job adopted from an instance that
                    # died before binding it keeps no launcher: it runs that
                    # instance's image, not this one's.
                    worker.launched_by = self.id
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
            self._watch_drain(worker)
            if reason is None:
                await self._mark_evictable(worker)
            ended = LostRuns()
            if reason is not None:
                # Its runs teach their workflows a larger request only when
                # Kubernetes stopped the worker for memory.
                out_of_memory = (
                    remote is not None
                    and worker.kubernetes_job_uid is not None
                    and oom_killed(await self.client.list_pods(worker.kubernetes_job_uid))
                )
                self._lose(worker, "out_of_memory" if out_of_memory else reason, now)
                ended = await end_runs_of_lost_worker(db, worker.id, out_of_memory=out_of_memory)
            await db.commit()
        await announce_lost_runs(ended)

    async def _mark_evictable(self, worker: ExecutionWorker) -> None:
        """Let the cluster autoscaler move a ready worker only while it is idle.

        Kubernetes decides where pods run and which node to empty; this only
        says when moving this worker interrupts nothing. The pod is patched
        only when the answer changes.
        """
        now = time.monotonic()
        if worker.state == "ready" and worker.memory_reserved_bytes == 0:
            since = self._idle_since.setdefault(worker.id, now)
            evictable = now - since >= EVICTABLE_AFTER_IDLE_SECONDS
        else:
            self._idle_since.pop(worker.id, None)
            evictable = False
        if self._evictable.get(worker.id) == evictable or worker.kubernetes_job_uid is None:
            return
        for pod in await self.client.list_pods(worker.kubernetes_job_uid):
            await self.client.annotate_pod(
                pod["metadata"]["name"], {SAFE_TO_EVICT: "true" if evictable else "false"}
            )
        self._evictable[worker.id] = evictable

    def _watch_drain(self, worker: ExecutionWorker) -> None:
        """Warn once when a draining worker's runs outlast the maximum
        lifetime: it still counts toward Max workers."""
        if worker.state != "draining" or worker.active_runs == 0:
            self._draining_since.pop(worker.id, None)
            return
        since = self._draining_since.setdefault(worker.id, time.monotonic())
        if (
            time.monotonic() - since >= self.settings.pool_max_lifetime_seconds
            and worker.id not in self._warned_long_drains
        ):
            self._warned_long_drains.add(worker.id)
            logger.warning(
                "A draining worker still has runs after its maximum lifetime",
                extra={
                    "worker_id": str(worker.id),
                    "active_runs": worker.active_runs,
                    "end_reason": worker.end_reason,
                },
            )

    @staticmethod
    def _lose(worker: ExecutionWorker, reason: str, now: datetime) -> None:
        logger.warning(
            "Reusable worker lost",
            extra={"worker_id": str(worker.id), "reason": reason, "state": worker.state},
        )
        worker.state = "lost"
        worker.end_reason = reason
        worker.ended_at = now

    def _sizing(self, policy: KubernetesPolicy, workers: Any) -> JobSizing:
        """Settings -> Kubernetes sizes at the baseline live Jobs report."""
        threshold = self.settings.memory_pressure_threshold
        return JobSizing.of(
            policy, threshold, job_baseline(workers, policy.worker_memory_mib * 2**20, threshold)
        )

    def _lifetime_handoff_imminent(self, worker: ExecutionWorker, now: datetime) -> bool:
        """Whether a standard warm Job needs its replacement started before it drains.

        ``ready_at`` precedes the worker's claim-loop lifetime clock, so it is
        a durable, conservative-early deadline for starting the replacement.
        The existing start-timeout window preserves a logical warm worker while
        the new Job initializes. Short lifetimes use at most half their
        lifetime as lead time, so a new worker is never itself immediately
        considered due for replacement.
        """
        if (
            worker.form != "job"
            or worker.resource_class != "standard"
            or worker.state != "ready"
            or worker.ready_at is None
        ):
            return False
        lead_seconds = min(
            self.settings.pool_start_timeout_seconds,
            self.settings.pool_max_lifetime_seconds // 2,
        )
        return worker.ready_at + timedelta(
            seconds=self.settings.pool_max_lifetime_seconds - lead_seconds
        ) <= now

    async def launch_for_demand(self, policy: KubernetesPolicy) -> list[UUID]:
        """Reserve incarnations for waiting work, sized by memory, within ceilings.

        Waiting requests (dispatches, and local platform jobs waiting for
        executor memory) are placed oldest first into the free budget of
        executors that can claim (a Job that has not reported its budget
        counts as the one expected). What does not fit goes into new
        standard Jobs, or a Job sized for one request a standard Job cannot
        hold (reserved for it when it is a dispatch). The warm minimum is
        kept, and Jobs never exceed Max workers.
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
                        WorkDispatch.handler.label("handler"),
                    )
                    .where(
                        WorkDispatch.assigned_worker_id.is_(None),
                        # Summaries use the API's memory domain. Launching a
                        # physical worker cannot add that capacity.
                        WorkDispatch.handler.in_(REMOTE_WORKER_HANDLER_NAMES),
                    )
                    .union_all(
                        select(
                            PlatformJob.id,
                            PlatformJob.memory_required_bytes,
                            PlatformJob.created_at,
                            literal(False),
                            # Any physical worker runs local platform jobs.
                            literal(None),
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
            sizing = self._sizing(policy, workers)

            requests = {row.id: row.memory_request_bytes for row in waiting}
            free: list[FreeBudget] = []
            covered: set[UUID] = set()
            for worker in workers:
                # Physical workers only; an API's summary executor never is.
                if "workflow.execute" not in worker.supported_handlers:
                    continue
                handlers = frozenset(worker.supported_handlers)
                if worker.state == "draining" or worker.memory_pressured:
                    continue
                # A worker launched for one dispatch holds it until it claims it.
                held = 0
                if worker.reserved_for_dispatch_id in requests:
                    covered.add(worker.reserved_for_dispatch_id)
                    held = requests[worker.reserved_for_dispatch_id]
                # A Job admitted but still starting its pool has not measured
                # its budget yet; until it reports, count the one expected.
                # A static executor counts only once ready and measured.
                if worker.form == "job" and (
                    worker.state in ("reserved", "starting") or worker.memory_budget_bytes is None
                ):
                    budget = sizing.budget_bytes(launch_limit(worker, sizing.standard_limit_bytes))
                    free.append(FreeBudget(max(0, budget - held), handlers))
                elif worker.state == "ready" and worker.memory_budget_bytes is not None:
                    free.append(FreeBudget(
                        max(0, worker.memory_budget_bytes - worker.memory_reserved_bytes - held),
                        handlers,
                    ))
            plan = _plan_launches(
                [
                    Request(row.id, row.memory_request_bytes, row.handler)
                    for row in waiting
                    if row.id not in covered
                ],
                free,
                standard_budget=sizing.standard_budget_bytes,
                largest_budget=sizing.largest_budget_bytes,
            )
            jobs = sum(1 for worker in workers if worker.form == "job")
            now = _now()
            # Only standard Jobs make up the warm minimum; a sized Job exists
            # for its large work and drains once idle. A standard Job near its
            # self-managed lifetime is excluded here so its handoff can start
            # before it stops claiming. An outdated Job still claims until it
            # drains, so replacements stay within Max workers.
            undrained = sum(
                1
                for worker in workers
                if worker.form == "job"
                and worker.resource_class == "standard"
                and worker.state != "draining"
                and not self.live.outdated(worker)
                and not self._lifetime_handoff_imminent(worker, now)
            )
            warm = max(0, policy.warm_workers - undrained - plan.standard - len(plan.sized))
            room = max(0, policy.max_workers - jobs)
            launched: list[UUID] = []
            for dispatch_id, request in plan.sized:
                if len(launched) >= room:
                    break
                launched.append(
                    self._reserve(
                        db,
                        resource_class="sized",
                        memory_limit_bytes=sizing.sized_limit_bytes(request),
                        reserved_for=dispatch_id if dispatch_id in dispatch_ids else None,
                    )
                )
            for _ in range(plan.standard + warm):
                if len(launched) >= room:
                    break
                launched.append(
                    self._reserve(
                        db,
                        resource_class="standard",
                        memory_limit_bytes=sizing.standard_limit_bytes,
                        reserved_for=None,
                    )
                )
            await db.commit()
        if launched:
            logger.info(
                "Launching reusable workers",
                extra={
                    "count": len(launched),
                    "waiting": len(waiting),
                    "sized": len(plan.sized),
                    "free_mib": sum(budget.bytes for budget in free) // 2**20,
                },
            )
        return launched

    async def replace_outdated(self, policy: KubernetesPolicy) -> list[UUID]:
        """Drain only old ready Jobs whose removal keeps the warm minimum.

        Current replacements launch first whenever a slot is available. If
        a full pool has no current standard Job making progress, standard
        ready Jobs above the warm minimum let one old Job drain and free an
        ordinary slot. Draining Jobs continue to count toward Max workers
        until they retire.
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
            jobs = (
                await db.execute(
                    select(ExecutionWorker).where(
                        ExecutionWorker.form == "job", ExecutionWorker.state.in_(LIVE_STATES)
                    )
                )
            ).scalars().all()
            ready = [worker for worker in jobs if worker.state == "ready"]
            outdated = [
                worker
                for worker in ready
                if self.live.outdated(worker) and worker.reserved_for_dispatch_id is None
            ]
            if not outdated:
                return []
            current_ready = sum(
                1
                for worker in ready
                if worker.resource_class == "standard"
                and worker.memory_budget_bytes is not None
                and not self.live.outdated(worker)
            )
            outdated_standard = [
                worker for worker in outdated if worker.resource_class == "standard"
            ]
            if policy.warm_workers == 0:
                drainable = outdated
            elif current_ready >= policy.warm_workers:
                drainable = outdated
            else:
                current_starting = any(
                    worker.resource_class == "standard"
                    and worker.state in ("reserved", "starting")
                    and not self.live.outdated(worker)
                    for worker in jobs
                )
                ready_standard = sum(
                    1
                    for worker in ready
                    if worker.resource_class == "standard"
                    and worker.memory_budget_bytes is not None
                )
                ready_excess = max(0, ready_standard - policy.warm_workers)
                drainable = (
                    outdated_standard[:ready_excess]
                    if len(jobs) >= policy.max_workers and not current_starting
                    else []
                )
            if not drainable:
                return []
            drained = list(
                (
                    await db.scalars(
                        update(ExecutionWorker)
                        .where(
                            ExecutionWorker.id.in_([worker.id for worker in drainable]),
                            ExecutionWorker.state == "ready",
                        )
                        .values(state="draining", end_reason="rotated")
                        .returning(ExecutionWorker.id)
                    )
                ).all()
            )
            await db.commit()
        if drained:
            logger.info(
                "Replacing workers running an older build",
                extra={"workers": [str(worker_id) for worker_id in drained], "version": self.version},
            )
        return drained

    def _reserve(
        self,
        db: Any,
        *,
        resource_class: str,
        memory_limit_bytes: int,
        reserved_for: UUID | None,
    ) -> UUID:
        """A durable row for one Job, recording the limit it launches with:
        a later Worker memory change applies to new Jobs only."""
        worker_id = uuid4()
        db.add(
            ExecutionWorker(
                id=worker_id,
                form="job",
                state="reserved",
                resource_class=resource_class,
                slots=0,
                memory_limit_bytes=memory_limit_bytes,
                reserved_for_dispatch_id=reserved_for,
                supported_handlers=list(REMOTE_WORKER_HANDLER_NAMES),
                kubernetes_namespace=self.client.namespace,
                kubernetes_job_name=worker_job_name(worker_id),
            )
        )
        return worker_id


@dataclass(frozen=True)
class LaunchPlan:
    standard: int
    sized: list[tuple[UUID, int]]


@dataclass(frozen=True)
class Request:
    """Waiting work: a dispatch (with its handler) or a local platform job (None)."""

    id: UUID
    bytes: int
    handler: str | None = None


@dataclass(frozen=True)
class FreeBudget:
    """An executor's free memory and the handlers it can claim."""

    bytes: int
    handlers: frozenset[str]

    def takes(self, request: Request) -> bool:
        return request.handler is None or request.handler in self.handlers


def _plan_launches(
    waiting: list[Request],
    free: list[FreeBudget],
    *,
    standard_budget: int,
    largest_budget: int,
) -> LaunchPlan:
    """Place requests (oldest first) into free budgets, then into new Jobs.

    First fit: each request takes the first budget that holds it, on an
    executor that can claim its handler. A request no standard Job can hold
    gets its own sized Job; one larger than the largest launchable budget is
    left for the reconciler to fail. New Jobs claim every handler.
    """
    rooms = [budget.bytes for budget in free]
    new_bins: list[int] = []
    sized: list[tuple[UUID, int]] = []
    for item in waiting:
        dispatch_id, request = item.id, item.bytes
        placed = False
        for index, budget in enumerate(free):
            if rooms[index] >= request and budget.takes(item):
                rooms[index] -= request
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


async def _publish_launchable(largest_budget: int) -> None:
    """Tell the dispatch reconciler the largest budget this controller can launch."""
    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        await redis.set(LAUNCHABLE_KEY, str(largest_budget), ex=LAUNCHABLE_TTL_SECONDS)


async def _publish_live(controller_id: UUID, version: str) -> LiveControllers:
    """Record this instance as live and read every instance that is."""
    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        # Redis's clock, so every instance's "at" compares on one clock.
        seconds, microseconds = await redis.time()  # type: ignore[misc]
        now = seconds + microseconds / 1_000_000
        await redis.hset(  # type: ignore[misc]
            CONTROLLERS_KEY, str(controller_id), json.dumps({"version": version, "at": now})
        )
        entries = await redis.hgetall(CONTROLLERS_KEY)  # type: ignore[misc]
        ids: set[UUID] = set()
        versions: set[str] = set()
        expired: list[str] = []
        for key, value in entries.items():
            entry = json.loads(value)
            if now - entry["at"] > HEARTBEAT_LOST_AFTER.total_seconds():
                expired.append(key)
                continue
            ids.add(UUID(key))
            versions.add(entry["version"])
        if expired:
            await redis.hdel(CONTROLLERS_KEY, *expired)  # type: ignore[misc]
    return LiveControllers(ids=frozenset(ids), versions=frozenset(versions))


async def _withdraw_live(controller_id: UUID) -> None:
    """A stopping controller is gone at once, not after the lost window."""
    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        await redis.hdel(CONTROLLERS_KEY, str(controller_id))  # type: ignore[misc]


async def worker_pool_loop(stop_event: asyncio.Event, settings: Any | None = None) -> None:
    settings = settings or get_settings()
    # Pool work waits visibly (and fails at its claim deadline) instead of
    # taking down the process that hosts this loop.
    try:
        client = KubernetesJobClient(settings)
    except KubernetesConfigurationError as exc:
        logger.error("Reusable worker controller disabled: %s", exc)
        await stop_event.wait()
        return
    try:
        spec = await resolve_job_spec_when_ready(
            client, settings, stop_event, "Reusable worker controller"
        )
        if spec is None:
            return
        controller = WorkerPoolController(client, settings, spec)
        wake = asyncio.Event()
        listener = asyncio.create_task(
            listen_for_pool_wake(settings.redis_url, wake), name="worker-pool-controller-wake"
        )
        try:
            while not stop_event.is_set():
                started = time.monotonic()
                try:
                    if await controller.reachability.check():
                        await controller.reconcile()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "Reusable worker reconciliation failed (%s): %s",
                        type(exc).__name__,
                        exc,
                    )
                await wait_for_next_pass(
                    stop_event, wake, since=started, spacing=RECONCILE_SECONDS, sweep=SWEEP_SECONDS
                )
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            try:
                await _withdraw_live(controller.id)
            except Exception:
                logger.warning("Could not withdraw the worker controller", exc_info=True)
    finally:
        await client.close()


__all__ = ["LiveControllers", "WorkerPoolController", "worker_pool_loop"]
