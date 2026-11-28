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
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, literal, select, text, update

from shared.version import get_version
from shared.worker_compute import WORKER_IMAGE_COMPUTE_CAPABILITY_NAMES
from src.config import Settings, get_settings
from src.core.database import get_db_context
from src.jobs.platform.catalog import executor_platform_job_types, get_platform_job_metadata
from src.jobs.platform.policy import EXTERNAL_ISOLATED_BACKEND
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
from src.services.worker_pool.demand import pending_capacity_platform_job_ids
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
from src.worker.remote.protocol import HANDLER_CONCURRENCY_LIMITS, handler_slot_available

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
        protected from replacement loops. External workers remain bound to
        the API/image build; controller identity alone cannot protect an
        outdated external worker. Unadmitted workers never rotate here.
        """
        return (
            worker.form in MANAGED_WORKER_FORMS
            and worker.state in ("ready", "draining")
            and worker.version not in self.versions
            and (worker.form == "external" or worker.launched_by not in self.ids)
        )


def _worker_can_claim_workflows(worker: ExecutionWorker) -> bool:
    """Whether a worker is a physical workflow-capacity participant."""
    return "workflow.execute" in worker.supported_handlers


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
    def _live_scope(self) -> str:
        """Stable identity for this provider's rolling-controller records."""
        raise NotImplementedError

    @abstractmethod
    def _owns_managed_worker(self, worker: ExecutionWorker) -> bool:
        """Whether a managed worker belongs to this provider incarnation."""
        raise NotImplementedError

    @abstractmethod
    def _binding_fields(self, worker_id: UUID) -> dict[str, Any]:
        """Persist provider identity before the resource can start."""
        raise NotImplementedError

    def _planner_execution_handlers(self) -> tuple[str, ...]:
        """Execution handlers a newly reserved worker can truthfully offer."""
        return REMOTE_WORKER_HANDLER_NAMES

    def _planner_compute_capabilities(self) -> tuple[str, ...]:
        """Semantic compute grants a newly reserved worker can truthfully offer."""
        return WORKER_IMAGE_COMPUTE_CAPABILITY_NAMES

    def _planner_platform_job_types(self) -> frozenset[str]:
        """Local platform types this provider may provision."""
        return executor_platform_job_types()

    def _worker_contributes_to_planner(self, worker: ExecutionWorker) -> bool:
        """Keep privileged worker behavior unchanged for existing consumers."""
        return _worker_can_claim_workflows(worker)

    def _max_managed_workers(self, policy: KubernetesPolicy) -> int:
        """The provider's share of the canonical managed-worker ceiling."""
        return policy.max_workers

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
        self.live = await _publish_live(self.id, self.version, self._live_scope())
        async with get_db_context() as db:
            policy = await load_kubernetes_policy(db)
        launched = await self.launch_for_demand(policy)
        await self.reconcile_lifecycle(policy, launched)

    async def reconcile_lifecycle(
        self, policy: KubernetesPolicy, launched: Collection[UUID] = ()
    ) -> None:
        """Reconcile this provider's resources after shared planning committed.

        Provider I/O intentionally happens after the planner's short database
        transaction.  The legacy one-provider ``reconcile`` above delegates
        here; a mixed-provider coordinator supplies its own reservations.
        """
        self.live = await _publish_live(self.id, self.version, self._live_scope())
        async with get_db_context() as db:
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
        for worker_id in dict.fromkeys([*ids, *launched]):
            await self._reconcile_one(worker_id, policy)
        await self.replace_outdated(policy)

    async def _reconcile_one(self, worker_id: UUID, policy: KubernetesPolicy) -> None:
        """One worker's failure (a slow Kubernetes API, a replaced Job) must
        not stop the others from being reconciled."""
        try:
            await self.reconcile_worker(worker_id, policy)
        except Exception as exc:
            logger.exception(
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
                        self._managed_scope(),
                        ExecutionWorker.state.in_(LIVE_STATES),
                        ExecutionWorker.isolated_platform_job_id.is_(None),
                    )
                )
            ).scalars().all()
            managed_count = int(
                await db.scalar(
                    select(func.count()).select_from(ExecutionWorker).where(
                        ExecutionWorker.form.in_(MANAGED_WORKER_FORMS),
                        ExecutionWorker.state.in_(LIVE_STATES),
                        ExecutionWorker.isolated_platform_job_id.is_(None),
                    )
                )
                or 0
            )
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
                if managed_count >= policy.max_workers and not current_starting:
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
        reserved_for_dispatch_id: UUID | None = None,
        isolated_job_id: UUID | None = None,
        isolated_job_attempt: int | None = None,
        isolated_compute: tuple[str, ...] | None = None,
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
                supported_handlers=([] if isolated_job_id is not None else list(self._planner_execution_handlers())),
                supported_compute=(
                    list(isolated_compute)
                    if isolated_compute is not None
                    else list(self._planner_compute_capabilities())
                ),
                execution_lane=execution_lane,
                reserved_for_dispatch_id=reserved_for_dispatch_id,
                isolated_platform_job_id=isolated_job_id,
                isolated_platform_job_attempt=isolated_job_attempt,
                **self._binding_fields(worker_id),
            )
        )
        return worker_id


    async def launch_for_demand(self, policy: KubernetesPolicy) -> list[UUID]:
        """Reserve demand through the provider-neutral locked planner."""
        return (await plan_capacity_launches(policy, (self,)))[self]


class CapacityProfileRefreshRequired(Exception):
    """Unwind admission before refreshing the provider used by this plan."""

    def __init__(self, refresh: Callable[[], Awaitable[None]]) -> None:
        super().__init__("Provider profile requires refresh")
        self.refresh = refresh


async def plan_capacity_launches(
    policy: KubernetesPolicy, providers: Collection[WorkerCapacityController],
) -> dict[WorkerCapacityController, list[UUID]]:
    """Refresh demanded providers outside the shared reservation transaction."""
    refreshed: set[Callable[[], Awaitable[None]]] = set()
    while True:
        try:
            return await _plan_capacity_launches(policy, providers)
        except CapacityProfileRefreshRequired as required:
            if required.refresh in refreshed:
                raise
            refreshed.add(required.refresh)
            await required.refresh()


async def _plan_capacity_launches(
    policy: KubernetesPolicy, providers: Collection[WorkerCapacityController],
) -> dict[WorkerCapacityController, list[UUID]]:
    """Reserve Normal capacity for several physical providers in one transaction.

    Every provider mix, including a single Kubernetes or Cloud provider, uses
    this one transaction and worker snapshot.  Callers must never compose
    per-provider passes: they would each see the same demand and over-reserve.

    Physical preparation remains the adapter's responsibility after this
    transaction commits.  A descriptor's order is its admission preference:
    Kubernetes is first, external capacity is overflow only.
    """
    ordered = tuple(providers)
    launched: dict[WorkerCapacityController, list[UUID]] = {provider: [] for provider in ordered}
    if not ordered:
        return launched
    async with get_db_context() as db:
        locked = (
            await db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": CAPACITY_LOCK})
        ).scalar_one()
        if not locked:
            return launched
        now = _now()
        handlers = frozenset().union(*(provider._planner_execution_handlers() for provider in ordered))
        platform_types = frozenset().union(*(provider._planner_platform_job_types() for provider in ordered))
        platform_job_ids = await pending_capacity_platform_job_ids(
            db, policy, now, eligible_job_types=platform_types,
            backends=("local", EXTERNAL_ISOLATED_BACKEND),
        )
        sources = [
            select(
                WorkDispatch.id.label("id"), WorkDispatch.memory_request_bytes.label("memory_request_bytes"),
                WorkDispatch.created_at.label("created_at"), WorkDispatch.handler.label("handler"),
                literal(None).label("compute_capability"), literal(None).label("job_type"),
                literal("local").label("backend"), literal(None).label("attempt"),
                literal("dispatch").label("source"),
            ).where(
                WorkDispatch.assigned_worker_id.is_(None), WorkDispatch.execution_lane == "normal",
                WorkDispatch.handler.in_(handlers),
            )
        ]
        if platform_job_ids:
            sources.append(select(
                PlatformJob.id, PlatformJob.memory_required_bytes, PlatformJob.created_at,
                literal(None), literal(None), PlatformJob.job_type,
                PlatformJob.execution_backend, PlatformJob.attempt, literal("platform"),
            ).where(
                PlatformJob.id.in_(platform_job_ids), PlatformJob.status == "queued",
                PlatformJob.execution_backend.in_(("local", EXTERNAL_ISOLATED_BACKEND)),
                PlatformJob.available_at <= now,
            ))
        if any("workflow.execute" in provider._planner_execution_handlers() for provider in ordered):
            sources.append(pending_service_statement(now).with_only_columns(
                ServiceDefinition.id, workflow_request_sql(Workflow), ServiceDefinition.updated_at,
                literal("workflow.execute"), literal(None), literal(None),
                literal("local"), literal(None), literal("service"),
            ))
        waiting = (await db.execute(sources[0].union_all(*sources[1:]).order_by(text("created_at")))).all()
        workers = (await db.execute(
            select(ExecutionWorker).where(ExecutionWorker.state.in_(LIVE_STATES)).with_for_update()
        )).scalars().all()
        generation = int(await db.scalar(select(func.coalesce(func.max(PackageChange.id), 0))) or 0)

        # Each provider owns its calibration identity.  A provider without a
        # compatible largest measurement is explicitly unknown, never evidence
        # that another provider's smaller measurement makes work impossible.
        sizing: dict[WorkerCapacityController, JobSizing] = {}
        largest: dict[WorkerCapacityController, int | None] = {}
        standard: dict[WorkerCapacityController, int] = {}
        known_limits: dict[WorkerCapacityController, set[int]] = {}
        blocked_limits: dict[WorkerCapacityController, set[int]] = {}
        cold_busy: dict[WorkerCapacityController, bool] = {}
        for provider in ordered:
            environment = provider._memory_environment(generation)
            history = (await db.scalars(select(ExecutionWorker).where(
                provider._managed_scope(), ExecutionWorker.state.in_((*LIVE_STATES, "retired")),
                ExecutionWorker.memory_calibration["environment"] == environment,
                ExecutionWorker.memory_calibration["measurement"].is_not(None),
            ).order_by(ExecutionWorker.created_at.desc()))).all()
            measurements = [sample for worker in history if (sample := measured_budget(worker, environment))]
            provider_sizing = provider._sizing(policy, [])
            if measurements:
                provider_sizing = replace(
                    provider_sizing, baseline_bytes=max(sample.baseline_bytes for sample in measurements),
                )
            sizing[provider] = provider_sizing
            standard_reports = [sample.budget_bytes for sample in measurements
                                if sample.limit_bytes == provider_sizing.standard_limit_bytes]
            largest_reports = [sample.budget_bytes for sample in measurements
                               if sample.limit_bytes == provider_sizing.largest_limit_bytes]
            standard[provider] = standard_reports[-1] if standard_reports else provider_sizing.standard_budget_bytes
            largest[provider] = largest_reports[-1] if largest_reports else None
            known_limits[provider] = {sample.limit_bytes for sample in measurements}
            failed = set((await db.scalars(select(ExecutionWorker.memory_limit_bytes).where(
                provider._managed_scope(), ExecutionWorker.state == "lost",
                ExecutionWorker.memory_budget_bytes.is_(None),
                ExecutionWorker.version == provider.version,
                ExecutionWorker.memory_calibration["environment"] == environment,
                ExecutionWorker.ended_at > now - timedelta(
                    seconds=provider.settings.pool_start_timeout_seconds,
                ),
            ))).all())
            probing = {
                worker.memory_limit_bytes
                for worker in workers
                if provider._owns_managed_worker(worker)
                and worker.memory_budget_bytes is None
                and worker.memory_calibration
                and worker.memory_calibration.get("environment") == environment
                and worker.memory_limit_bytes not in known_limits[provider]
            }
            blocked_limits[provider] = failed | probing
            cold_busy[provider] = not measurements and bool(probing)

        request_rows = [
            (
                Request(
                    row.id,
                    row.memory_request_bytes,
                    row.handler,
                    row.compute_capability
                    if row.job_type is None
                    else (
                        get_platform_job_metadata(row.job_type).compute_capability
                        if get_platform_job_metadata(row.job_type) is not None
                        else None
                    ),
                    row.attempt + 1 if row.backend == EXTERNAL_ISOLATED_BACKEND else None,
                    frozenset(
                        get_platform_job_metadata(row.job_type).additional_compute_capabilities
                        if row.job_type is not None and get_platform_job_metadata(row.job_type) is not None
                        else ()
                    ),
                ),
                row.job_type,
                row.id if row.source == "dispatch" and row.handler == "workflow.execute" else None,
            )
            for row in waiting
        ]
        dispatch_requests = {
            dispatch_id: request
            for request, _job_type, dispatch_id in request_rows
            if dispatch_id is not None
        }

        def accepts(provider: WorkerCapacityController, request: Request, job_type: str | None) -> bool:
            if request.handler is not None:
                return request.handler in provider._planner_execution_handlers()
            return job_type in provider._planner_platform_job_types() and (
                request.required_compute_capabilities.issubset(provider._planner_compute_capabilities())
            )

        kubernetes = next((provider for provider in ordered if provider.worker_form == "job"), None)
        local_path_present = kubernetes is not None or any(
            worker.form not in MANAGED_WORKER_FORMS for worker in workers
        )

        def cloudflare_start_selected(
            provider: WorkerCapacityController, request: Request, job_type: str | None,
        ) -> bool:
            """Whether this request may start a new Cloudflare worker.

            Rules affect only new reservations. Existing external bins were
            deliberately considered first and may continue serving every
            capability their worker truthfully advertised.
            """
            if provider.worker_form != "external" or not local_path_present:
                return True
            rules = getattr(provider, "rules", None)
            if rules is None:
                return True
            if request.handler == "workflow.execute":
                return rules.workflows
            if request.handler in {"agent.run", "agent.summarize", "agent.summarize_backfill"}:
                return rules.agents
            if job_type is None or request.compute_capability is None:
                return False
            if job_type.startswith("application."):
                return rules.app_builds
            return rules.other_jobs

        def reservation_providers(
            request: Request, job_type: str | None,
        ) -> tuple[WorkerCapacityController, ...]:
            candidates = tuple(
                provider
                for provider in ordered
                if accepts(provider, request, job_type)
                and cloudflare_start_selected(provider, request, job_type)
            )
            cloudflare = tuple(provider for provider in candidates if provider.worker_form == "external")
            if not cloudflare or any(
                getattr(getattr(provider, "rules", None), "prefer_kubernetes", False)
                for provider in cloudflare
            ):
                return candidates
            return cloudflare + tuple(provider for provider in candidates if provider.worker_form != "external")

        # Real static and already-reserved/ready workers are placed before new
        # capacity.  Managed rows retain their own advertised capability rather
        # than being attributed to whichever provider happens to plan next.
        bins: list[FreeBudget] = []
        bin_owners: list[WorkerCapacityController | None] = []
        bin_workers: list[ExecutionWorker | None] = []
        covered: set[UUID] = set()
        for worker in workers:
            if (
                worker.isolated_platform_job_id is not None
                or
                worker.execution_lane != "normal"
                or worker.state == "draining"
                or worker.memory_pressured
            ):
                continue
            if worker.state not in ("reserved", "starting", "ready"):
                continue
            handler_slots = dict(HANDLER_CONCURRENCY_LIMITS)
            if worker.state == "ready":
                if worker.active_handler_counts is None:
                    handler_slots = {name: 0 for name in handler_slots}
                else:
                    handler_slots = {
                        name: limit - worker.active_handler_counts.get(name, 0)
                        for name, limit in HANDLER_CONCURRENCY_LIMITS
                    }
            owner: WorkerCapacityController | None = None
            if worker.form in MANAGED_WORKER_FORMS:
                owner = next((provider for provider in ordered if provider._owns_managed_worker(worker)), None)
                if owner is None:
                    continue
            if owner is not None and not owner._worker_contributes_to_planner(worker):
                continue
            # Static API executors can run summary maintenance, but only
            # physical worker capacity may stand in for the worker pool.
            if owner is None and not _worker_can_claim_workflows(worker):
                continue
            held = 0
            held_request = dispatch_requests.get(worker.reserved_for_dispatch_id)
            if held_request is not None:
                covered.add(held_request.id)
                held = held_request.bytes
                if held_request.handler in handler_slots:
                    handler_slots[held_request.handler] -= 1
            elif worker.reserved_for_dispatch_id is not None:
                # Cancellation removes a WorkDispatch. Do not leave the
                # physical worker stranded behind its obsolete reservation.
                worker.reserved_for_dispatch_id = None
            if worker.form in MANAGED_WORKER_FORMS and (
                worker.state in ("reserved", "starting") or worker.memory_budget_bytes is None
            ):
                budget = sizing[owner].budget_bytes(launch_limit(worker, sizing[owner].standard_limit_bytes))
            elif worker.state == "ready" and worker.memory_budget_bytes is not None:
                budget = worker.memory_budget_bytes - worker.memory_reserved_bytes
            else:
                continue
            bins.append(FreeBudget(max(0, budget - held), frozenset(worker.supported_handlers),
                                   frozenset(worker.supported_compute), handler_slots))
            bin_owners.append(owner)
            bin_workers.append(worker)

        rooms = [bin.bytes for bin in bins]
        slots = [dict(bin.handler_slots) for bin in bins]
        consumed_bins: set[int] = set()
        limited = dict(HANDLER_CONCURRENCY_LIMITS)
        managed_total = sum(
            worker.form in MANAGED_WORKER_FORMS and worker.isolated_platform_job_id is None
            for worker in workers
        )
        provider_count = {
            provider: sum(provider._owns_managed_worker(worker) for worker in workers)
            for provider in ordered
        }
        for provider in ordered:
            if provider.worker_form == "external":
                provider_count[provider] = int(await db.scalar(
                    select(func.count()).select_from(ExecutionWorker).where(
                        provider._managed_scope(), ExecutionWorker.capacity_resource_deleted_at.is_(None),
                    )
                ) or 0)
        unknown = {provider for provider in ordered if largest[provider] is None}

        async def publish_launchability() -> None:
            """Publish once from this locked provider and demand snapshot."""
            compatible_unknown = any(
                provider in unknown and accepts(provider, request, job_type)
                for request, job_type, _dispatch_id in request_rows
                for provider in ordered
            )
            measured = [budget for budget in largest.values() if budget is not None]
            launchable = None if compatible_unknown else (max(measured) if measured else None)
            if len(ordered) == 1:
                await _publish_launchable(launchable, generation)
                return
            fingerprint = ",".join(f"{provider._live_scope()}@{provider.version}" for provider in ordered)
            await _publish_launchable(launchable, generation, fingerprint=fingerprint)

        # Fast Path is an exclusive Kubernetes reservation.  Commit its floor
        # before normal demand so a burst cannot consume its final slots.
        primary = kubernetes or ordered[0]
        fast_path_launches = 0
        if getattr(primary, "worker_form", None) == "job":
            fast = sum(
                worker.form == "job" and primary._owns_managed_worker(worker)
                and worker.execution_lane == "fast_path" and worker.state != "draining"
                and not primary.live.outdated(worker)
                and not primary._lifetime_handoff_imminent(worker, now)
                for worker in workers
            )
            fast_needed = max(0, policy.reserved_fast_path_workers - fast)
            if managed_total >= policy.max_workers and fast_needed:
                normal_cap = policy.max_workers - policy.reserved_fast_path_workers
                normal_live = [
                    worker
                    for worker in workers
                    if primary._owns_managed_worker(worker)
                    and worker.execution_lane == "normal"
                    and worker.state != "draining"
                ]
                excess = max(0, len(normal_live) - normal_cap)
                candidates = [
                    worker
                    for worker in normal_live
                    if worker.state == "ready"
                    and worker.reserved_for_dispatch_id is None
                ]
                for worker in candidates[:excess]:
                    await db.execute(update(ExecutionWorker).where(
                        ExecutionWorker.id == worker.id,
                        ExecutionWorker.state == "ready",
                        ExecutionWorker.reserved_for_dispatch_id.is_(None),
                    ).values(state="draining", end_reason="fast_path_reservation"))
                if candidates[:excess]:
                    await publish_launchability()
                    await db.commit()
                    return launched
            for _ in range(fast_needed):
                if managed_total >= policy.max_workers:
                    break
                launched[primary].append(primary._reserve(
                    db, resource_class="standard", memory_limit_bytes=sizing[primary].standard_limit_bytes,
                    environment=primary._memory_environment(generation), execution_lane="fast_path",
                ))
                managed_total += 1
                fast_path_launches += 1

        # An external isolated resource remains bound through loss and
        # retirement until the provider deletion receipt lands. A later retry
        # must not prepare another physical resource for that same job first.
        from src.services.platform_jobs import publish_platform_job_update
        from src.services.worker_pool.unfit import fail_unfit_platform_job

        changed_isolated: list[PlatformJob] = []
        bound_isolated = set((await db.scalars(select(
            ExecutionWorker.isolated_platform_job_id,
        ).where(
            ExecutionWorker.isolated_platform_job_id.is_not(None),
            ExecutionWorker.capacity_resource_deleted_at.is_(None),
        ))).all())

        async def drain_idle_external_for_isolated(
            provider: WorkerCapacityController,
        ) -> bool:
            """Drain one unused standard external worker to free its profile.

            A preceding drain remains a physical reservation until the
            provider confirms deletion, so never start a second drain while it
            is unresolved. ``consumed_bins`` prevents stealing capacity that
            an older request already planned to use this pass.
            """
            prior_drain = await db.scalar(select(ExecutionWorker.id).where(
                provider._managed_scope(),
                ExecutionWorker.end_reason == "isolated_capacity",
                ExecutionWorker.capacity_resource_deleted_at.is_(None),
            ))
            if prior_drain is not None:
                return False
            for index, worker in enumerate(bin_workers):
                if (
                    worker is None
                    or bin_owners[index] is not provider
                    or index in consumed_bins
                    or rooms[index] != bins[index].bytes
                    or worker.form != "external"
                    or worker.state != "ready"
                    or worker.resource_class != "standard"
                    or worker.active_runs != 0
                    or worker.memory_reserved_bytes != 0
                    or worker.active_handler_counts is None
                    or any(worker.active_handler_counts.values())
                    or worker.reserved_for_dispatch_id is not None
                    or worker.isolated_platform_job_id is not None
                    or worker.memory_budget_bytes is None
                    or worker.memory_budget_bytes <= 0
                ):
                    continue
                drained = await db.scalar(update(ExecutionWorker).where(
                    ExecutionWorker.id == worker.id,
                    ExecutionWorker.state == "ready",
                    ExecutionWorker.active_runs == 0,
                    ExecutionWorker.memory_reserved_bytes == 0,
                    ExecutionWorker.reserved_for_dispatch_id.is_(None),
                    ExecutionWorker.isolated_platform_job_id.is_(None),
                ).values(state="draining", end_reason="isolated_capacity").returning(ExecutionWorker.id))
                return drained is not None
            return False

        async def reserve_isolated(
            request: Request,
            job_type: str | None,
        ) -> bool:
            """Reserve or route one ordered external-isolated request.

            True means the pass drained a reusable external resource and must
            wait for its confirmed deletion before planning more capacity.
            """
            if job_type is None or request.isolated_attempt is None or request.id in bound_isolated:
                return False
            job = await db.scalar(select(PlatformJob).where(
                PlatformJob.id == request.id,
                PlatformJob.status == "queued",
                PlatformJob.execution_backend == EXTERNAL_ISOLATED_BACKEND,
                PlatformJob.attempt == request.isolated_attempt - 1,
                PlatformJob.available_at <= now,
            ).with_for_update(skip_locked=True))
            if job is None:
                return False
            metadata = get_platform_job_metadata(job.job_type)
            if metadata is None or metadata.compute_capability is None:
                return False
            if not policy.isolated_jobs_enabled or job.job_type not in policy.enabled_job_types:
                job.execution_backend, job.phase = "local", "Queued"
                job.revision += 1
                changed_isolated.append(job)
                return False
            known_unfit: list[int] = []
            external_candidates = 0
            for provider in reservation_providers(request, job_type):
                if provider.worker_form == "job":
                    # Kubernetes retains its existing queued-job lifecycle;
                    # it will mint the attempt lease when its pod claims it.
                    job.execution_backend, job.phase = "kubernetes", "Queued"
                    job.revision += 1
                    changed_isolated.append(job)
                    return False
                # Exclusive resources exist only on the external adapter.
                # A future provider must opt into this lifecycle explicitly;
                # it cannot accidentally receive an isolated reservation.
                if provider.worker_form != "external":
                    continue
                external_candidates += 1
                if largest[provider] is not None and request.bytes > largest[provider]:
                    known_unfit.append(largest[provider])
                    continue
                if provider_count[provider] >= provider._max_managed_workers(policy):
                    if await drain_idle_external_for_isolated(provider):
                        return True
                    continue
                limit = sizing[provider].standard_limit_bytes
                if limit in blocked_limits[provider] or cold_busy[provider]:
                    continue
                worker_id = provider._reserve(
                    db,
                    resource_class="isolated",
                    memory_limit_bytes=limit,
                    environment=provider._memory_environment(generation),
                    isolated_job_id=job.id,
                    isolated_job_attempt=request.isolated_attempt,
                    isolated_compute=tuple(sorted(metadata.required_compute_capabilities)),
                )
                launched[provider].append(worker_id)
                provider_count[provider] += 1
                bound_isolated.add(job.id)
                if limit not in known_limits[provider]:
                    blocked_limits[provider].add(limit)
                if not known_limits[provider]:
                    cold_busy[provider] = True
                return False
            if known_unfit and len(known_unfit) == external_candidates:
                await fail_unfit_platform_job(db, job, max(known_unfit))
                changed_isolated.append(job)
            return False

        unplaced: list[tuple[Request, str | None, UUID | None]] = []
        drained_for_isolated = False
        for request, job_type, dispatch_id in request_rows:
            if request.id in covered:
                continue
            if request.isolated_attempt is not None:
                drained_for_isolated = await reserve_isolated(request, job_type)
                if drained_for_isolated:
                    break
                continue
            placed = False
            for index, bin in enumerate(bins):
                owner = bin_owners[index]
                if job_type is not None and owner is not None and job_type not in owner._planner_platform_job_types():
                    continue
                if (rooms[index] >= request.bytes and bin.takes(request)
                        and handler_slot_available(request.handler, slots[index])):
                    rooms[index] -= request.bytes
                    if request.handler in limited:
                        slots[index][request.handler] -= 1
                    consumed_bins.add(index)
                    placed = True
                    break
            if placed:
                continue
            for provider in reservation_providers(request, job_type):
                if managed_total >= policy.max_workers or not accepts(provider, request, job_type):
                    continue
                # External providers expose a real individual ceiling; other
                # providers share only the canonical global cap.
                if provider_count[provider] >= provider._max_managed_workers(policy):
                    continue
                predicted_largest = largest[provider] or sizing[provider].largest_budget_bytes
                if request.bytes > predicted_largest and provider not in unknown:
                    continue
                limit = sizing[provider].pod_limit_bytes(request.bytes)
                calibration_probe = limit is None
                if limit is None:
                    limit = sizing[provider].largest_limit_bytes
                if limit in blocked_limits[provider] or cold_busy[provider]:
                    continue
                worker_id = provider._reserve(
                    db, resource_class="standard" if limit == sizing[provider].standard_limit_bytes else "sized",
                    memory_limit_bytes=limit, environment=provider._memory_environment(generation),
                    reserved_for_dispatch_id=(
                        dispatch_id
                        if (
                            dispatch_id is not None
                            and not calibration_probe
                            and request.bytes > standard[provider]
                        )
                        else None
                    ),
                )
                launched[provider].append(worker_id)
                provider_count[provider] += 1
                managed_total += 1
                if limit not in known_limits[provider]:
                    blocked_limits[provider].add(limit)
                if not known_limits[provider]:
                    cold_busy[provider] = True
                bins.append(FreeBudget(
                    standard[provider] if limit == sizing[provider].standard_limit_bytes else predicted_largest,
                    frozenset(provider._planner_execution_handlers()),
                    frozenset(provider._planner_compute_capabilities()),
                ))
                bin_owners.append(provider)
                bin_workers.append(None)
                rooms.append(max(0, bins[-1].bytes - request.bytes))
                new_slots = dict(limited)
                if request.handler in limited:
                    new_slots[request.handler] -= 1
                slots.append(new_slots)
                break
            else:
                unplaced.append((request, job_type, dispatch_id))

        if not drained_for_isolated and managed_total >= policy.max_workers and unplaced:
            ready = [
                worker
                for worker in workers
                if worker.form in MANAGED_WORKER_FORMS
                and worker.state == "ready"
                and worker.active_runs == 0
                and worker.memory_reserved_bytes == 0
                and worker.active_handler_counts is not None
                and not any(worker.active_handler_counts.values())
                and worker.reserved_for_dispatch_id is None
                and worker.isolated_platform_job_id is None
                and worker.memory_budget_bytes is not None
            ]
            for worker in ready:
                owner = next((provider for provider in ordered if provider._owns_managed_worker(worker)), None)
                if owner is None:
                    continue
                if not any(
                    request.bytes > worker.memory_budget_bytes
                    and accepts(owner, request, job_type)
                    for request, job_type, _dispatch_id in unplaced
                ):
                    continue
                await db.execute(update(ExecutionWorker).where(
                    ExecutionWorker.id == worker.id,
                    ExecutionWorker.state == "ready",
                    ExecutionWorker.active_runs == 0,
                    ExecutionWorker.memory_reserved_bytes == 0,
                ).values(state="draining", end_reason="capacity_replacement"))
                await publish_launchability()
                await db.commit()
                for job in changed_isolated:
                    await publish_platform_job_update(job)
                return launched

        # Only Kubernetes may supply the fixed Fast Path reservation.  Warm
        # normal capacity likewise stays primary so Cloud is true overflow.
        primary = kubernetes or ordered[0]
        if not drained_for_isolated and managed_total < policy.max_workers:
            normal = sum(
                primary._owns_managed_worker(worker) and worker.execution_lane == "normal"
                and worker.resource_class == "standard" and worker.state != "draining"
                and not primary.live.outdated(worker)
                and not primary._lifetime_handoff_imminent(worker, now)
                for worker in workers
            ) + len(launched[primary]) - fast_path_launches
            warm_launches = max(0, policy.warm_workers - normal)
            if primary in unknown:
                warm_launches = min(1, warm_launches)
            if sizing[primary].standard_limit_bytes in blocked_limits[primary]:
                warm_launches = 0
            for _ in range(warm_launches):
                if (managed_total >= policy.max_workers
                        or provider_count[primary] >= primary._max_managed_workers(policy)):
                    break
                launched[primary].append(primary._reserve(
                    db, resource_class="standard", memory_limit_bytes=sizing[primary].standard_limit_bytes,
                    environment=primary._memory_environment(generation),
                ))
                managed_total += 1
                provider_count[primary] += 1
        await publish_launchability()
        await db.commit()
    for job in changed_isolated:
        await publish_platform_job_update(job)
    return launched


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
            ExecutionWorker.isolated_platform_job_id.is_(None),
        )

    def _live_scope(self) -> str:
        return f"job:{self.client.namespace}"

    def _owns_managed_worker(self, worker: ExecutionWorker) -> bool:
        return (worker.form == "job" and worker.kubernetes_namespace == self.client.namespace
                and worker.isolated_platform_job_id is None)

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
    """Waiting dispatch, service, or platform work with explicit eligibility."""

    id: UUID
    bytes: int
    handler: str | None = None
    compute_capability: str | None = None
    # The exact next PlatformJob attempt an exclusive external worker may
    # claim. ``None`` means reusable dispatch/service/local-job capacity.
    isolated_attempt: int | None = None
    additional_compute_capabilities: frozenset[str] = field(default_factory=frozenset)

    @property
    def required_compute_capabilities(self) -> frozenset[str]:
        return self.additional_compute_capabilities.union(
            () if self.compute_capability is None else (self.compute_capability,),
        )


@dataclass(frozen=True)
class FreeBudget:
    """An executor's free memory, capabilities, and limited handler slots."""

    bytes: int
    handlers: frozenset[str]
    compute_capabilities: frozenset[str] = field(default_factory=frozenset)
    handler_slots: dict[str, int] = field(default_factory=lambda: dict(HANDLER_CONCURRENCY_LIMITS))

    def takes(self, request: Request) -> bool:
        return (
            (request.handler is None or request.handler in self.handlers)
            and request.required_compute_capabilities.issubset(self.compute_capabilities)
        )


def _plan_launches(
    waiting: list[Request],
    free: list[FreeBudget],
    *,
    standard_budget: int,
    largest_budget: int,
    cold: bool = False,
    probe_largest: bool = False,
    new_handlers: Collection[str] | None = None,
    new_compute_capabilities: Collection[str] | None = None,
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
                and handler_slot_available(item.handler, slots[index])
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
        bins.append(FreeBudget(
            standard_budget,
            frozenset(new_handlers) if new_handlers is not None else frozenset(REMOTE_WORKER_HANDLER_NAMES),
            frozenset(new_compute_capabilities) if new_compute_capabilities is not None else frozenset(),
        ))
        rooms.append(standard_budget - request)
        new_slots = dict(limited)
        if item.handler in limited:
            new_slots[item.handler] -= 1
        slots.append(new_slots)
    return LaunchPlan(standard=len(bins) - len(free), sized=sized)


async def _publish_launchable(
    largest_budget: int | None, generation: int, *, fingerprint: str | None = None,
) -> None:
    """Tell the dispatch reconciler the largest budget this controller can launch."""
    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        # Empty means a managed provider is present but its capacity is not
        # measured yet. It is distinct from no active capacity controller.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.set(LAUNCHABLE_KEY, "" if largest_budget is None else str(largest_budget),
                     ex=LAUNCHABLE_TTL_SECONDS)
            value = "" if largest_budget is None else str(largest_budget)
            # Keep the two-field encoding for single-provider compatibility;
            # the mixed controller adds its ordered provider/version identity.
            encoded = f"{generation}:{value}"
            if fingerprint is not None:
                encoded = f"{encoded}:{fingerprint}"
            pipe.set(LAUNCHABLE_GENERATION_KEY, encoded, ex=LAUNCHABLE_TTL_SECONDS)
            await pipe.execute()


async def _publish_live(
    controller_id: UUID, version: str, provider_scope: str
) -> LiveControllers:
    """Record this instance as live and read every instance that is."""
    from src.core.cache.redis_client import get_redis

    async with get_redis() as redis:
        # Redis's clock, so every instance's "at" compares on one clock.
        seconds, microseconds = await redis.time()  # type: ignore[misc]
        now = seconds + microseconds / 1_000_000
        await redis.hset(  # type: ignore[misc]
            CONTROLLERS_KEY,
            str(controller_id),
            json.dumps({"version": version, "scope": provider_scope, "at": now}),
        )
        entries = await redis.hgetall(CONTROLLERS_KEY)  # type: ignore[misc]
        ids: set[UUID] = set()
        versions: set[str] = set()
        expired: list[str] = []
        for key, value in entries.items():
            try:
                entry = json.loads(value)
                scope = entry["scope"]
                entry_version = entry["version"]
                at = entry["at"]
            except (KeyError, TypeError, ValueError):
                expired.append(key)
                continue
            if (
                not isinstance(scope, str)
                or not isinstance(entry_version, str)
                or not isinstance(at, (int, float))
            ):
                expired.append(key)
                continue
            if now - at > HEARTBEAT_LOST_AFTER.total_seconds():
                expired.append(key)
                continue
            if scope != provider_scope:
                continue
            ids.add(UUID(key))
            versions.add(entry_version)
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
        logger.exception("Reusable worker controller disabled: %s", exc)
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
                    logger.exception(
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


__all__ = [
    "LiveControllers", "WorkerCapacityController", "WorkerPoolController", "worker_pool_loop",
    "plan_capacity_launches",
]
