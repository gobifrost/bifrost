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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, literal, select, text, update

from shared.version import get_version
from src.config import Settings, get_settings
from src.core.database import get_db_context
from src.jobs.platform.kubernetes_client import (
    KubernetesConfigurationError,
    KubernetesJobClient,
    oom_killed,
)
from src.models.orm.execution_workers import MANAGED_WORKER_FORMS, ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.package_changes import PackageChange
from src.models.orm.services import ServiceDefinition
from src.models.orm.work_dispatches import WorkDispatch
from src.models.orm.workflows import Workflow
from src.services.kubernetes_execution import KubernetesPolicy, load_kubernetes_policy
from src.services.memory_requests import LAUNCHABLE_GENERATION_KEY, LAUNCHABLE_KEY, workflow_request_sql
from src.services.service_lifecycle import pending_service_statement
from src.services.work_dispatch import (
    DISPATCH_WAKE_FAMILIES,
    WAKE_PLATFORM_JOB,
    WAKE_SERVICE,
    listen_for_pool_wake,
    wait_for_next_pass,
)
from src.services.worker_pool.claims import CAPACITY_LOCK, REMOTE_WORKER_HANDLER_NAMES
from src.services.worker_pool.demand import pending_local_platform_job_ids
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
from src.services.worker_pool.calibration import measured_budget
from src.worker.remote.protocol import HANDLER_CONCURRENCY_LIMITS

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
        """Rotate admitted workers once no live controller advertises their build.

        Kubernetes controllers have a fixed build, so their own Jobs remain
        protected from replacement loops. External controllers can adopt a
        new provider build without restarting; their identity cannot protect
        workers of the previous build. Unadmitted workers never rotate here.
        """
        return (
            worker.form in MANAGED_WORKER_FORMS
            and worker.state in ("ready", "draining")
            and worker.version not in self.versions
            and (worker.form == "external" or worker.launched_by not in self.ids)
        )


class WorkerCapacityController(ABC):
    """Shared reservation, demand, rotation and lifecycle policy."""

    def __init__(self, settings: Settings, *, worker_form: str) -> None:
        self.settings = settings
        self.worker_form = worker_form
        self.cleanup_states = ("lost",)
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

    @abstractmethod
    def _managed_scope(self) -> Any:
        """SQL predicate selecting resources this configured provider owns."""
        raise NotImplementedError

    @abstractmethod
    def _binding_fields(self, worker_id: UUID) -> dict[str, Any]:
        """Persist provider identity before the resource can start."""
        raise NotImplementedError

    @abstractmethod
    async def reconcile_worker(self, worker_id: UUID, policy: KubernetesPolicy) -> None:
        """Reconcile one physical resource without changing domain ownership."""
        raise NotImplementedError

    def _cleanup_scope(self) -> Any:
        return (
            ExecutionWorker.state.in_(self.cleanup_states)
            & (ExecutionWorker.ended_at >= _now() - LOST_JOB_CLEANUP)
        )

    async def reconcile(self) -> None:
        self.live = await _publish_live(self.id, self.version)
        async with get_db_context() as db:
            policy = await load_kubernetes_policy(db)
            ids = (
                await db.scalars(
                    select(ExecutionWorker.id).where(
                        self._managed_scope(),
                        ExecutionWorker.state.in_(LIVE_STATES) | self._cleanup_scope(),
                    )
                )
            ).all()
        for worker_id in set(self._draining_since) - set(ids):
            del self._draining_since[worker_id]
        for worker_id in set(self._evictable) - set(ids):
            self._evictable.pop(worker_id, None)
            self._idle_since.pop(worker_id, None)
        # Refresh capacity under its lock before any provider I/O can stall.
        # New rows are included in this pass; provider losses free their slots
        # for the next bounded reconciliation pass.
        launched = await self.launch_for_demand(policy)
        for worker_id in dict.fromkeys([*ids, *launched]):
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
            worker.form not in MANAGED_WORKER_FORMS
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

        Waiting requests (dispatches, pending services, and eligible local
        platform jobs) are placed oldest first into the free budget of
        executors that can claim (a Job that has not reported its budget
        counts as the one expected). What does not fit goes into new
        standard Jobs, or a Job sized for one request a standard Job cannot
        hold. Actual claims always wait for measured fit. Idle insufficient
        workers can yield the warm minimum; Jobs never exceed Max workers.
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
            platform_job_ids = await pending_local_platform_job_ids(db, policy, _now())
            waiting = (
                await db.execute(
                    select(
                        WorkDispatch.id.label("id"),
                        WorkDispatch.memory_request_bytes.label("memory_request_bytes"),
                        WorkDispatch.created_at.label("created_at"),
                        WorkDispatch.handler.label("handler"),
                    )
                    .where(
                        WorkDispatch.assigned_worker_id.is_(None),
                        WorkDispatch.execution_lane == "normal",
                        # Every advertised execution handler, including live
                        # and backfill summaries, gains physical capacity.
                        WorkDispatch.handler.in_(REMOTE_WORKER_HANDLER_NAMES),
                    )
                    .union_all(
                        select(
                            PlatformJob.id,
                            PlatformJob.memory_required_bytes,
                            PlatformJob.created_at,
                            # Any physical worker runs local platform jobs.
                            literal(None),
                        ).where(
                            PlatformJob.id.in_(platform_job_ids),
                            PlatformJob.status == "queued",
                            PlatformJob.execution_backend == "local",
                            PlatformJob.available_at <= _now(),
                        ),
                        pending_service_statement(_now()).with_only_columns(
                            ServiceDefinition.id,
                            workflow_request_sql(Workflow),
                            ServiceDefinition.updated_at,
                            # Every physical executor supervises services.
                            literal(None),
                        ),
                    )
                    .order_by(text("created_at"))
                )
            ).all()
            workers = (
                await db.execute(
                    select(ExecutionWorker).where(ExecutionWorker.state.in_(LIVE_STATES)).with_for_update()
                )
            ).scalars().all()
            generation = int(await db.scalar(select(func.coalesce(func.max(PackageChange.id), 0))) or 0)
            environment = self._memory_environment(generation)
            history = (await db.scalars(select(ExecutionWorker).where(
                self._managed_scope(),
                ExecutionWorker.state.in_((*LIVE_STATES, "retired")),
                ExecutionWorker.memory_calibration["environment"] == environment,
                ExecutionWorker.memory_calibration["measurement"].is_not(None),
            ).distinct(ExecutionWorker.memory_limit_bytes).order_by(
                ExecutionWorker.memory_limit_bytes, ExecutionWorker.created_at.desc(),
            ))).all()
            measurements = [sample for worker in history
                            if (sample := measured_budget(worker, environment)) is not None]
            sizing = self._sizing(policy, [])
            if measurements:
                sizing = replace(sizing, baseline_bytes=max(sample.baseline_bytes for sample in measurements))
            standard_reports = [sample.budget_bytes for sample in measurements
                                if sample.limit_bytes == sizing.standard_limit_bytes]
            largest_reports = [sample.budget_bytes for sample in measurements
                               if sample.limit_bytes == sizing.largest_limit_bytes]
            confirmed_largest = largest_reports[-1] if largest_reports else None
            # Publication and decisions share one ordered snapshot. Predictions
            # choose resources; only a measured largest size can reject work.
            await _publish_launchable(confirmed_largest, generation)

            requests = {row.id: row.memory_request_bytes for row in waiting}
            request_handlers = {row.id: row.handler for row in waiting}
            free: list[FreeBudget] = []
            covered: set[UUID] = set()
            for worker in workers:
                # Only physical workers contribute launcher capacity.
                if (
                    "workflow.execute" not in worker.supported_handlers
                    or worker.execution_lane != "normal"
                ):
                    continue
                handlers = frozenset(worker.supported_handlers)
                if worker.state == "draining" or worker.memory_pressured:
                    continue
                # A worker launched for one dispatch holds it until it claims it.
                held = 0
                handler_slots = dict(HANDLER_CONCURRENCY_LIMITS)
                if worker.state not in ("reserved", "starting"):
                    if worker.active_handler_counts is None:
                        handler_slots = {handler: 0 for handler in handler_slots}
                    else:
                        handler_slots = {
                            handler: max(0, limit - worker.active_handler_counts.get(handler, 0))
                            for handler, limit in HANDLER_CONCURRENCY_LIMITS
                        }
                if worker.reserved_for_dispatch_id in requests:
                    covered.add(worker.reserved_for_dispatch_id)
                    held = requests[worker.reserved_for_dispatch_id]
                    held_handler = request_handlers[worker.reserved_for_dispatch_id]
                    if held_handler in handler_slots:
                        handler_slots[held_handler] = max(0, handler_slots[held_handler] - 1)
                # A Job admitted but still starting its pool has not measured
                # its budget yet; until it reports, count the one expected.
                # A static executor counts only once ready and measured.
                if worker.form in MANAGED_WORKER_FORMS and (
                    worker.state in ("reserved", "starting") or worker.memory_budget_bytes is None
                ):
                    budget = sizing.budget_bytes(launch_limit(worker, sizing.standard_limit_bytes))
                    free.append(FreeBudget(max(0, budget - held), handlers, handler_slots))
                elif worker.state == "ready" and worker.memory_budget_bytes is not None:
                    free.append(FreeBudget(
                        max(0, worker.memory_budget_bytes - worker.memory_reserved_bytes - held),
                        handlers, handler_slots,
                    ))
            plan = _plan_launches(
                [
                    Request(row.id, row.memory_request_bytes, row.handler)
                    for row in waiting
                    if row.id not in covered
                ],
                free,
                standard_budget=standard_reports[-1] if standard_reports else sizing.standard_budget_bytes,
                largest_budget=confirmed_largest if confirmed_largest is not None else sizing.largest_budget_bytes,
                cold=not measurements, probe_largest=confirmed_largest is None,
            )
            jobs = sum(1 for worker in workers if worker.form in MANAGED_WORKER_FORMS)
            now = _now()
            # Only standard Jobs make up the warm minimum; a sized Job exists
            # for its large work and drains once idle. A standard Job near its
            # self-managed lifetime is excluded here so its handoff can start
            # before it stops claiming. An outdated Job still claims until it
            # drains, so replacements stay within Max workers.
            undrained = sum(
                1
                for worker in workers
                if worker.form in MANAGED_WORKER_FORMS
                and worker.execution_lane == "normal"
                and worker.resource_class == "standard"
                and worker.state != "draining"
                and not self.live.outdated(worker)
                and not self._lifetime_handoff_imminent(worker, now)
            )
            warm = max(0, policy.warm_workers - undrained - plan.standard - len(plan.sized))
            room = max(0, policy.max_workers - jobs)
            # Fast Path capacity is a fixed exclusive reservation, not an
            # elastic overflow lane. It intentionally ignores background
            # demand and therefore cannot be borrowed by it.
            fast_path_undrained = sum(
                1
                for worker in workers
                if worker.form in MANAGED_WORKER_FORMS
                and worker.execution_lane == "fast_path"
                and worker.resource_class == "standard"
                and worker.state != "draining"
                and not self.live.outdated(worker)
                and not self._lifetime_handoff_imminent(worker, now)
            )
            fast_path_warm = max(0, policy.reserved_fast_path_workers - fast_path_undrained)
            # Keep the reservation's slots out of the Normal planner. Without
            # this, a Normal burst arriving in the same pass could consume the
            # final slots before the Fast Path floor is reserved.
            normal_room = max(0, room - fast_path_warm)
            # First initialization is singular; it may be sized by the hint,
            # but an uncertain prediction can never veto that initial attempt.
            if not measurements and any(
                (worker.state in ("reserved", "starting") or
                 (worker.state == "ready" and worker.memory_budget_bytes is None))
                and worker.memory_calibration
                and worker.memory_calibration.get("environment") == environment
                for worker in workers if worker.form in MANAGED_WORKER_FORMS
            ):
                return []
            # Pace failures per resource size without disabling recovery. An
            # unmeasured size gets only one probe at a time, even after another
            # size has already calibrated the environment.
            failed_limits = set((await db.scalars(select(ExecutionWorker.memory_limit_bytes).where(
                self._managed_scope(), ExecutionWorker.state == "lost",
                ExecutionWorker.memory_budget_bytes.is_(None),
                ExecutionWorker.version == self.version,
                ExecutionWorker.memory_calibration["environment"] == environment,
                ExecutionWorker.ended_at > _now() - timedelta(seconds=self.settings.pool_start_timeout_seconds),
            ))).all())
            known_limits = {sample.limit_bytes for sample in measurements}
            probing_limits = {
                worker.memory_limit_bytes for worker in workers
                if worker.form in MANAGED_WORKER_FORMS and worker.memory_budget_bytes is None
                and worker.memory_calibration
                and worker.memory_calibration.get("environment") == environment
                and worker.memory_limit_bytes not in known_limits
            }
            blocked_limits = failed_limits | probing_limits
            plan = LaunchPlan(
                standard=0 if sizing.standard_limit_bytes in blocked_limits else plan.standard,
                sized=[(identity, request) for identity, request in plan.sized
                       if (sizing.pod_limit_bytes(request) or sizing.largest_limit_bytes) not in blocked_limits],
            )
            if sizing.standard_limit_bytes in blocked_limits:
                warm = 0
            if room == 0 and fast_path_warm:
                # A Settings transition can reserve slots that were already
                # occupied by Normal workers. Drain only idle Normal workers
                # above the new Normal cap; active work is never preempted and
                # Fast Path capacity reports degraded until a slot is freed.
                normal_cap = policy.max_workers - policy.reserved_fast_path_workers
                normal_live = [
                    worker for worker in workers
                    if worker.form in MANAGED_WORKER_FORMS
                    and worker.execution_lane == "normal"
                    and worker.state != "draining"
                ]
                excess = max(0, len(normal_live) - normal_cap)
                if excess:
                    owned = set((await db.scalars(select(ExecutionWorker.id).where(
                        self._managed_scope(), ExecutionWorker.state == "ready"
                    ))).all())
                    candidates = [
                        worker for worker in normal_live
                        if worker.id in owned and worker.state == "ready"
                        # Draining is non-preemptive: current work keeps
                        # running, but this excess Normal worker cannot claim
                        # another dispatch while the reservation converges.
                        and not worker.reserved_for_dispatch_id
                    ]
                    for worker in candidates[:excess]:
                        await db.execute(update(ExecutionWorker).where(
                            ExecutionWorker.id == worker.id, ExecutionWorker.state == "ready",
                            ExecutionWorker.reserved_for_dispatch_id.is_(None),
                        ).values(state="draining", end_reason="fast_path_reservation"))
                    await db.commit()
                    return []
            if room == 0 and (plan.sized or plan.standard):
                # An idle undersized worker may yield the warm minimum, but it
                # still occupies its slot until the canonical owner retires it.
                owned = set((await db.scalars(select(ExecutionWorker.id).where(self._managed_scope(), ExecutionWorker.state == "ready"))).all())
                ready = [worker for worker in workers
                         if worker.id in owned and worker.state == "ready"
                         and worker.active_runs == 0 and worker.memory_reserved_bytes == 0
                         and worker.active_handler_counts is not None
                         and not any(worker.active_handler_counts.values())
                         and not worker.reserved_for_dispatch_id
                         and worker.memory_budget_bytes is not None]
                needed = [row for row in waiting if row.id not in covered]
                for worker in ready:
                    if not any(row.memory_request_bytes > worker.memory_budget_bytes
                               and (row.handler is None or row.handler in worker.supported_handlers)
                               for row in needed):
                        continue
                    await db.execute(update(ExecutionWorker).where(
                        ExecutionWorker.id == worker.id, ExecutionWorker.state == "ready",
                        ExecutionWorker.active_runs == 0, ExecutionWorker.memory_reserved_bytes == 0,
                    ).values(state="draining", end_reason="capacity_replacement"))
                    break
                await db.commit()
                return []
            launched: list[UUID] = []
            for _dispatch_id, request in plan.sized:
                if len(launched) >= normal_room:
                    break
                limit = sizing.pod_limit_bytes(request) or sizing.largest_limit_bytes
                if limit in probing_limits:
                    continue
                if limit not in known_limits:
                    probing_limits.add(limit)
                launched.append(
                    self._reserve(
                        db,
                        resource_class="standard" if limit == sizing.standard_limit_bytes else "sized",
                        memory_limit_bytes=limit,
                        # New workers claim through measured fit, including
                        # sized workers whose actual overhead differs.
                        environment=environment,
                    )
                )
            standard_launches = plan.standard + warm
            if sizing.standard_limit_bytes not in known_limits:
                standard_launches = min(1, standard_launches)
            for _ in range(standard_launches):
                if len(launched) >= normal_room:
                    break
                launched.append(
                    self._reserve(
                        db,
                        resource_class="standard",
                        memory_limit_bytes=sizing.standard_limit_bytes,
                        environment=environment,
                    )
                )
            for _ in range(fast_path_warm):
                if len(launched) >= room:
                    break
                launched.append(
                    self._reserve(
                        db,
                        resource_class="standard",
                        memory_limit_bytes=sizing.standard_limit_bytes,
                        environment=environment,
                        execution_lane="fast_path",
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
                        ExecutionWorker.form.in_(MANAGED_WORKER_FORMS), ExecutionWorker.state.in_(LIVE_STATES)
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
            lane_floor = {
                "normal": policy.warm_workers,
                "fast_path": policy.reserved_fast_path_workers,
            }
            # Rotation must not borrow a current Normal worker to replace an
            # old Fast Path worker (or the reverse): each lane keeps its own
            # ready floor during a rolling replacement.
            current_ready = {
                lane: sum(
                    1
                    for worker in ready
                    if worker.execution_lane == lane
                    and worker.resource_class == "standard"
                    and worker.memory_budget_bytes is not None
                    and not self.live.outdated(worker)
                )
                for lane in lane_floor
            }
            drainable: list[ExecutionWorker] = []
            for lane, floor in lane_floor.items():
                lane_outdated = [worker for worker in outdated if worker.execution_lane == lane]
                if floor == 0 or current_ready[lane] >= floor:
                    drainable.extend(lane_outdated)
                    continue
                # At the hard total cap, one outdated standard worker above
                # this lane's own floor can make room for its replacement.
                # Do not use another lane's ready count for that decision.
                current_starting = any(
                    worker.execution_lane == lane
                    and worker.resource_class == "standard"
                    and worker.state in ("reserved", "starting")
                    and not self.live.outdated(worker)
                    for worker in jobs
                )
                ready_standard = sum(
                    1
                    for worker in ready
                    if worker.execution_lane == lane
                    and worker.resource_class == "standard"
                    and worker.memory_budget_bytes is not None
                )
                excess = max(0, ready_standard - floor)
                if len(jobs) >= policy.max_workers and not current_starting:
                    drainable.extend(
                        worker
                        for worker in lane_outdated[:excess]
                        if worker.resource_class == "standard"
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

    def _memory_environment(self, generation: int) -> dict[str, Any]:
        return {"form": self.worker_form, "version": self.version,
                "package_generation": generation, "threshold": self.settings.memory_pressure_threshold}

    def _reserve(
        self,
        db: Any,
        *,
        resource_class: str,
        memory_limit_bytes: int,
        environment: dict[str, Any],
        execution_lane: str = "normal",
    ) -> UUID:
        """A durable row for one Job, recording the limit it launches with:
        a later Worker memory change applies to new Jobs only."""
        worker_id = uuid4()
        db.add(
            ExecutionWorker(
                id=worker_id,
                form=self.worker_form,
                state="reserved",
                resource_class=resource_class,
                memory_limit_bytes=memory_limit_bytes,
                memory_calibration={"environment": environment},
                supported_handlers=list(REMOTE_WORKER_HANDLER_NAMES),
                execution_lane=execution_lane,
                **self._binding_fields(worker_id),
            )
        )
        return worker_id


class WorkerPoolController(WorkerCapacityController):
    """Kubernetes adapter for the shared physical-worker controller."""

    def __init__(
        self, client: KubernetesJobClient, settings: Settings, spec: WorkerJobSpec,
    ) -> None:
        super().__init__(settings, worker_form="job")
        self.client = client
        self.spec = spec

    def _memory_environment(self, generation: int) -> dict[str, Any]:
        return {**super()._memory_environment(generation), "image": self.spec.image}

    def _managed_scope(self) -> Any:
        return and_(
            ExecutionWorker.form == "job",
            ExecutionWorker.kubernetes_namespace == self.client.namespace,
        )

    def _binding_fields(self, worker_id: UUID) -> dict[str, Any]:
        return {
            "kubernetes_namespace": self.client.namespace,
            "kubernetes_job_name": worker_job_name(worker_id),
            # Remember the intended build even if startup fails before hello.
            # Admission still replaces it with the physical worker's report.
            "version": self.version,
        }

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
                            execution_lane=worker.execution_lane,
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
    """An executor's free memory, capabilities, and limited handler slots."""

    bytes: int
    handlers: frozenset[str]
    handler_slots: dict[str, int] = field(default_factory=lambda: dict(HANDLER_CONCURRENCY_LIMITS))

    def takes(self, request: Request) -> bool:
        return request.handler is None or request.handler in self.handlers


def _plan_launches(
    waiting: list[Request],
    free: list[FreeBudget],
    *,
    standard_budget: int,
    largest_budget: int,
    cold: bool = False,
    probe_largest: bool = False,
) -> LaunchPlan:
    """Place requests (oldest first) into free budgets, then into new Jobs.

    First fit: each request takes the first budget that holds it, on an
    executor that can claim its handler. A request no standard Job can hold
    gets its own sized Job; one larger than the largest launchable budget is
    left for the reconciler to fail only after the largest size was measured.
    Cold planning chooses one initial resource; uncertain large requests probe
    the largest configured size. New Jobs claim every handler.
    """
    bins = list(free)
    rooms = [budget.bytes for budget in bins]
    slots = [dict(budget.handler_slots) for budget in bins]
    limited = dict(HANDLER_CONCURRENCY_LIMITS)
    sized: list[tuple[UUID, int]] = []
    for item in waiting:
        dispatch_id, request = item.id, item.bytes
        placed = False
        for index, budget in enumerate(bins):
            if (
                rooms[index] >= request and budget.takes(item)
                and (item.handler not in limited or slots[index].get(item.handler, 0) > 0)
            ):
                rooms[index] -= request
                if item.handler in limited:
                    slots[index][item.handler] -= 1
                placed = True
                break
        if placed:
            continue
        if cold:
            return (LaunchPlan(standard=1, sized=[]) if request <= standard_budget
                    else LaunchPlan(standard=0, sized=[(dispatch_id, request)]))
        if request > standard_budget:
            if request <= largest_budget or probe_largest:
                sized.append((dispatch_id, request))
            continue
        bins.append(FreeBudget(standard_budget, frozenset(REMOTE_WORKER_HANDLER_NAMES)))
        rooms.append(standard_budget - request)
        new_slots = dict(limited)
        if item.handler in limited:
            new_slots[item.handler] -= 1
        slots.append(new_slots)
    return LaunchPlan(standard=len(bins) - len(free), sized=sized)


async def _publish_launchable(largest_budget: int | None, generation: int) -> None:
    """Tell the dispatch reconciler the largest budget this controller can launch."""
    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        # Empty means a managed provider is present but its capacity is not
        # measured yet. It is distinct from no active capacity controller.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.set(LAUNCHABLE_KEY, "" if largest_budget is None else str(largest_budget),
                     ex=LAUNCHABLE_TTL_SECONDS)
            value = "" if largest_budget is None else str(largest_budget)
            pipe.set(LAUNCHABLE_GENERATION_KEY, f"{generation}:{value}", ex=LAUNCHABLE_TTL_SECONDS)
            await pipe.execute()


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
            listen_for_pool_wake(
                settings.redis_url,
                {
                    **{family: wake for family in DISPATCH_WAKE_FAMILIES},
                    WAKE_PLATFORM_JOB: wake,
                    WAKE_SERVICE: wake,
                },
            ),
            name="worker-pool-controller-wake",
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


__all__ = ["LiveControllers", "WorkerCapacityController", "WorkerPoolController", "worker_pool_loop"]
