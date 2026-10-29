"""Pool executor: claims runs from PostgreSQL and runs each in a fresh child.

Two forms share this loop. A *job* worker is started by the worker-pool
controller as one Kubernetes Job lifetime (it carries an incarnation). A
*static* worker is a long-running container (the ``scheduler`` and ``worker``
services, through their entry points) that registers itself and never idles
out. A remote executor runs workflows, agent runs, services and local platform
jobs in its launcher's children; the API-resident executor runs summaries.

A job worker admits itself
to its incarnation (Job UID bound before start, pod UID bound once), then
claims pool dispatches from PostgreSQL and hands each to the ordinary
consumer code, which runs it in a fresh child of the process pool. With no
work for its Idle time (Settings -> Kubernetes), or past
``pool_max_lifetime_seconds``, it moves itself to ``draining`` (after which it can never claim), lets active
runs finish, retires and exits 0. The controller drains it the same way
(after an upgrade). SIGTERM drains too, but its runs get only
``DRAIN_GRACE_SECONDS`` before the pod is killed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from shared.schema_gate import wait_for_schema
from shared.version import get_version
from src.config import Settings, get_settings
from src.core.database import close_db, get_db_context, get_engine, init_db
from src.core.redis_client import PendingExecution
from src.jobs.schedulers.platform_jobs import (
    ClaimedPlatformJob,
    claim_platform_job,
    recover_expired_platform_jobs,
    run_claimed_platform_job,
)
from src.scheduler.control import replica_heartbeat_loop
from src.services.kubernetes_control import start_kubernetes_loops
from src.services.kubernetes_execution import (
    import_kubernetes_policy_from_env,
    load_kubernetes_policy,
)
from src.services.scheduler_diagnostics import remove_scheduler_replica
from src.jobs.summarize_worker import handle_summarize_message
from src.services.memory_requests import (
    WORKFLOW_REQUEST_MARGIN_BYTES,
    MemoryBudget,
    measure_budget,
    under_pressure,
)
from src.worker.admission import Admission
from src.services.package_generation import (
    change_message,
    current_package_generation,
    package_changes_after,
)
from src.services.worker_pool.claims import (
    PoolClaim,
    SUPPORTED_HANDLERS,
    admit_worker,
    begin_drain,
    begin_idle_drain,
    claim_next,
    heartbeat_worker,
    mark_static_ready,
    register_static_worker,
    retire_worker,
)
from src.services.worker_pool.recovery import UNSTARTED_CLAIM_WINDOW, release_unstarted_claims

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class AgentConsumer(Protocol):
    """Runs one claimed ``agent.run`` dispatch and returns when it has ended."""

    async def process_message(self, body: dict) -> None:
        """Run the dispatch in ``body``."""
        raise NotImplementedError


# While an executor holds work (room can free up at any moment) or admission
# holds it back (memory pressure, a reconnect still reconciling), it looks for
# work every POLL_SECONDS. Idle with nothing to claim, it waits for the wake-up
# that every hand-off publishes, and sweeps every SWEEP_SECONDS in case a
# wake-up was lost (Redis pub/sub does not guarantee delivery).
POLL_SECONDS = 1.0
SWEEP_SECONDS = 15.0
HEARTBEAT_SECONDS = 5.0
# How long a job worker uses the Idle time it read from Settings -> Kubernetes.
POLICY_REFRESH_SECONDS = 10.0
DRAIN_GRACE_SECONDS = 300.0
# How often the platform-job loop looks while it runs jobs or admission holds
# it back; idle, it waits for a wake-up and sweeps every SWEEP_SECONDS, which
# is also how often it recovers jobs whose runner lease expired.
PLATFORM_JOB_POLL_SECONDS = 2.0


def executor_database_settings(settings: Settings) -> Settings:
    """Keep every connection the executor may open, instead of discarding overflow.

    An executor uses the database on every claim and every run. SQLAlchemy
    closes overflow connections when they are returned, so with a small pool
    each run reconnected (and re-read asyncpg's type catalogue) on the hot
    path. The total stays ``pool_size + max_overflow``; only the churn goes.
    """
    return settings.model_copy(
        update={
            "database_pool_size": settings.database_pool_size + settings.database_max_overflow,
            "database_max_overflow": 0,
        }
    )


class PoolWorker:
    def __init__(
        self,
        *,
        incarnation_id: UUID | None = None,
        job_uid: str | None = None,
        pod_uid: str | None = None,
        on_ready: Callable[[], None] | None = None,
        handlers: tuple[tuple[str, int], ...] = SUPPORTED_HANDLERS,
        admission: Admission | None = None,
        hosts_remote_work: bool = True,
    ) -> None:
        """A job worker passes its incarnation and UIDs; a static one, none.

        ``on_ready`` runs once the executor can claim work (an entry point's
        readiness signal).
        """
        self.settings = get_settings()
        self.static = incarnation_id is None
        self.incarnation_id: UUID = incarnation_id or UUID(int=0)
        self.job_uid = job_uid
        self.pod_uid = pod_uid
        self._on_ready = on_ready
        self._handlers = handlers
        # A remote executor owns a process pool, services, package updates,
        # and local platform jobs. The API-resident agent executor owns none
        # of those: it only runs agent consumers in the API process.
        self.hosts_remote_work = hosts_remote_work
        self.runs_handled = 0
        self._stop = asyncio.Event()
        # SIGTERM: the pod is being killed, so a drain's runs get a grace.
        self._terminating = asyncio.Event()
        self._wake = asyncio.Event()
        self._platform_wake = asyncio.Event()
        self._service_wake = asyncio.Event()
        self._package_wake = asyncio.Event()
        # Whether the last claim attempt was held back by admission.
        self._admission_blocked = False
        # In-flight consumer calls by handler. A workflow call returns once its
        # run is routed to a child (then the pool counts it); agent runs and
        # summaries return when they end.
        self._tasks: dict[str, set[asyncio.Task[None]]] = {
            "workflow.execute": set(),
            "agent.run": set(),
            "agent.summarize": set(),
            "agent.summarize_backfill": set(),
        }
        self._workflow_consumer = None
        self._agent_consumer: AgentConsumer | None = None
        self._package_consumer = None
        self.package_generation = 0
        self._service_claim_loop = None
        # Measured during executor startup (src.services.memory_requests).
        self.budget: MemoryBudget | None = None
        # A measured budget is not usable capacity until executor startup has
        # completed. Remote executors can await inventory reconciliation after
        # measuring it, while the controller treats a published budget as a
        # ready replacement that may let an old worker drain.
        self._claim_ready = False
        # The memory gate every claim goes through (src.worker.admission):
        # workflow runs are held by execution id until their child ends,
        # other work by dispatch, platform job or service attempt id.
        self.admission = admission
        # Ids of the local platform jobs this executor is running.
        self._platform_jobs: set[UUID] = set()
        # Set to stop claiming platform jobs; running ones finish.
        self._platform_stop = asyncio.Event()
        # Run and dispatch ids this executor started recently (by start time),
        # to tell them from a claim whose reply it lost.
        self._started: dict[str, float] = {}
        # The control plane declared this executor lost and ended its runs:
        # stop them instead of letting them finish (their results are refused).
        self._lost = False
        # Idle time from Settings -> Kubernetes, with when it was read.
        self._idle_seconds_value = 0
        self._idle_seconds_read_at: float | None = None

    def request_stop(self) -> None:
        """SIGTERM or SIGINT: stop claiming and drain within the grace."""
        self._terminating.set()
        self._stop.set()

    async def _idle_seconds(self) -> int:
        """Idle time, read again at most every ``POLICY_REFRESH_SECONDS``."""
        now = time.monotonic()
        if (
            self._idle_seconds_read_at is None
            or now - self._idle_seconds_read_at >= POLICY_REFRESH_SECONDS
        ):
            async with get_db_context() as db:
                self._idle_seconds_value = (await load_kubernetes_policy(db)).idle_seconds
            self._idle_seconds_read_at = now
        return self._idle_seconds_value

    def _workflow_runs(self) -> int:
        """Runs with a child, plus claimed runs still being routed to one."""
        pool_runs = (
            self._workflow_consumer._pool.active_execution_count()
            if self._workflow_consumer is not None
            else 0
        )
        routing = self.admission.starting_workflows() if self.admission is not None else 0
        return pool_runs + routing

    def _services(self) -> int:
        if self._workflow_consumer is None:
            return 0
        return len(self._workflow_consumer._pool.service_processes)

    def busy(self) -> bool:
        """Runs, services or platform jobs: an executor with any is not idle."""
        return self.active_runs() > 0 or self._services() > 0 or bool(self._platform_jobs)

    def active_runs(self) -> int:
        return self._workflow_runs() + sum(
            len(tasks) for handler, tasks in self._tasks.items() if handler != "workflow.execute"
        )

    def _hold_service_memory(self, attempt_id: str, request_bytes: int | None) -> None:
        """A supervised service holds its request until its attempt ends."""
        assert self.admission is not None
        if request_bytes is None:
            self.admission.release(f"service:{attempt_id}")
        else:
            self.admission.hold(f"service:{attempt_id}", request_bytes)

    def _claimable(self) -> tuple[tuple[str, int], ...]:
        """Handlers this executor may claim; the claim takes only work whose
        request fits the free budget (``Admission``). One live summary plus
        one backfill summary at a time is policy, so a large backfill never
        delays live summaries."""
        handlers = [pair for pair in self._handlers if pair[0] in {"workflow.execute", "agent.run"}]
        for summary_handler in ("agent.summarize", "agent.summarize_backfill"):
            if (summary_handler, 1) in self._handlers and not self._tasks[summary_handler]:
                handlers.append((summary_handler, 1))
        return tuple(handlers)

    async def _initialize_runtime(self) -> bool:
        """Initialize this process's database runtime and join the worker pool."""
        get_engine(executor_database_settings(self.settings))
        await init_db()
        await wait_for_schema(get_engine())
        from sqlalchemy.orm import configure_mappers

        configure_mappers()
        if self.static:
            self.incarnation_id = await register_static_worker(
                hostname=socket.gethostname(),
                supported_handlers=[handler for handler, _version in self._handlers],
            )
        else:
            assert self.job_uid is not None and self.pod_uid is not None
            if await admit_worker(
                self.incarnation_id, job_uid=self.job_uid, pod_uid=self.pod_uid,
                version=get_version(),
            ) is None:
                logger.error(
                    "Pool worker was not admitted (not this Job's first pod)",
                    extra={"incarnation_id": str(self.incarnation_id)},
                )
                return False
        return True

    async def _close_runtime(self) -> None:
        """Release the database runtime owned by this executor process."""
        await close_db()

    async def _start_background_control(self) -> list[asyncio.Task[None]]:
        """Start static-executor Kubernetes control loops when this host owns them."""
        if not self.static or not self.hosts_remote_work:
            return []
        # On an install whose executor container may manage Jobs (the builds
        # runbook binds that Role to the scheduler), it runs the Kubernetes
        # loops; elsewhere this starts nothing.
        async with get_db_context() as db:
            await import_kubernetes_policy_from_env(db, os.environ)
        return await start_kubernetes_loops(self._platform_stop, self.settings)

    def _measure_budget(self) -> MemoryBudget:
        return measure_budget(self.settings.memory_pressure_threshold)

    def _under_pressure(self) -> bool:
        return under_pressure(self.settings.memory_pressure_threshold)

    def _claims_open(self) -> bool:
        """Whether the executor may claim new work at all (memory aside)."""
        return True

    def _make_workflow_consumer(self):  # type: ignore[no-untyped-def]
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        return WorkflowExecutionConsumer()

    def _make_package_consumer(self):  # type: ignore[no-untyped-def]
        from src.jobs.consumers.package_install import PackageInstallConsumer

        return PackageInstallConsumer()

    def _make_agent_consumer(self) -> AgentConsumer:
        from src.jobs.consumers.agent_run import AgentRunConsumer

        return AgentRunConsumer()

    async def _execute_platform_job(self, claim: ClaimedPlatformJob) -> bool:
        return await run_claimed_platform_job(claim)

    async def _claim_platform_job(self, fits_bytes: int) -> ClaimedPlatformJob | None:
        return await claim_platform_job(backend="local", fits_bytes=fits_bytes)

    async def run(self) -> int:
        if not await self._initialize_runtime():
            return 1

        # Heartbeat from registration on: starting the pool (and installing
        # its requirements) can take minutes, longer than a silent executor
        # is given before it counts as lost.
        heartbeat = asyncio.create_task(self._heartbeat_loop(), name="pool-heartbeat")
        try:
            await self._start_executor()
            self._claim_ready = True
        except BaseException:
            heartbeat.cancel()
            raise
        if self.static:
            assert self.budget is not None
            # Not ready if it was declared lost while starting: the claim
            # loop then claims nothing and the heartbeat stops it.
            await mark_static_ready(
                self.incarnation_id, memory_budget_bytes=self.budget.budget_bytes
            )
        background = [
            heartbeat,
            asyncio.create_task(self._wake_listener(), name="pool-wake"),
            asyncio.create_task(
                replica_heartbeat_loop(
                    replica_id=str(self.incarnation_id),
                    runs_platform_jobs=self.hosts_remote_work,
                    stop_event=self._platform_stop,
                ),
                name="pool-diagnostics-heartbeat",
            ),
        ]
        if self.hosts_remote_work:
            background.extend(
                [
                    asyncio.create_task(self._package_loop(), name="pool-packages"),
                    asyncio.create_task(self._platform_job_loop(), name="pool-platform-jobs"),
                ]
            )
            background.extend(await self._start_background_control())
        if self._on_ready is not None:
            self._on_ready()
        try:
            await self._claim_loop()
            await self._drain()
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await self._retire()
            try:
                await remove_scheduler_replica(str(self.incarnation_id))
            except Exception:
                logger.exception("Failed to remove the executor's diagnostics heartbeat")
            if self._workflow_consumer is not None:
                await self._workflow_consumer.stop_pool()
            await self._close_runtime()
        logger.info(
            "Pool worker retired",
            extra={"incarnation_id": str(self.incarnation_id), "runs": self.runs_handled},
        )
        return 0

    async def _retire(self) -> None:
        """Retire this worker incarnation after it has drained.

        A remote launcher can be adopted by another API owner while the old
        owner is draining. Such a host overrides this hook to detach when its
        session fence is stale rather than retiring the launcher's durable
        worker row.
        """
        await retire_worker(self.incarnation_id, runs_handled=self.runs_handled)

    async def _start_executor(self) -> None:
        """Start the pool, consumers, memory gate and service supervision."""
        if self.hosts_remote_work:
            # Read the package generation before the pool installs requirements:
            # a change committed in between is then applied again (installs are
            # idempotent) instead of being missed.
            self.package_generation = await current_package_generation()
            self._workflow_consumer = self._make_workflow_consumer()
            self._workflow_consumer.owner_worker_id = self.incarnation_id
            await self._workflow_consumer.start_pool()
            # Applies recorded package changes; it never consumes the broadcast.
            self._package_consumer = self._make_package_consumer()
        if any(handler == "agent.run" for handler, _version in self._handlers):
            self._agent_consumer = self._make_agent_consumer()
        # Measured once the parent and fork template are up, before any
        # service claims: what remains is what work may use. API-resident
        # agents receive the API host's shared gate at construction time.
        self.budget = self._measure_budget()
        if self.admission is None:
            if self._workflow_consumer is None:
                self.admission = Admission(
                    budget_bytes=self.budget.budget_bytes,
                    under_pressure=self._under_pressure,
                    running_workflow_ids=set,
                    workflow_growth=dict,
                    claims_open=self._claims_open,
                )
            else:
                pool = self._workflow_consumer._pool
                self.admission = Admission(
                    budget_bytes=self.budget.budget_bytes,
                    under_pressure=self._under_pressure,
                    running_workflow_ids=pool.active_execution_ids,
                    workflow_growth=pool.workflow_growth,
                    claims_open=self._claims_open,
                )
        if self._workflow_consumer is not None:
            pool = self._workflow_consumer._pool
            pool.memory_kill_growth_bytes = max(
                0, self.budget.budget_bytes - WORKFLOW_REQUEST_MARGIN_BYTES
            )
            await self._start_service_claim_loop()
        logger.info(
            "Pool worker ready",
            extra={
                "incarnation_id": str(self.incarnation_id),
                "budget_mib": self.budget.budget_bytes // 2**20,
                "limit_mib": self.budget.limit_bytes // 2**20,
            },
        )

    async def _claim_loop(self) -> None:
        started = time.monotonic()
        last_activity = time.monotonic()
        while not self._stop.is_set():
            # A database error (a failover, a dropped connection) must not
            # end the executor and its runs: back off and look again.
            try:
                # Clear before looking, so a hint that lands during the claim
                # attempt still wakes the wait below instead of being lost.
                self._wake.clear()
                assert self.admission is not None
                claim = await self._claim_and_start()
                if claim is not None:
                    last_activity = time.monotonic()
                    continue
                if self.busy():
                    last_activity = time.monotonic()
                elif (
                    not self.static
                    and time.monotonic() - last_activity >= await self._idle_seconds()
                ):
                    if await begin_idle_drain(self.incarnation_id):
                        return
                    # Part of the warm minimum: stay ready and look again later.
                    last_activity = time.monotonic()
                if (
                    not self.static
                    and time.monotonic() - started >= self.settings.pool_max_lifetime_seconds
                ):
                    await begin_drain(self.incarnation_id, "max_lifetime")
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pool worker claim failed; retrying")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=HEARTBEAT_SECONDS)
                await self._release_unstarted_claims()
                continue
            stop = asyncio.ensure_future(self._stop.wait())
            wake = asyncio.ensure_future(self._wake.wait())
            await asyncio.wait(
                {stop, wake},
                timeout=POLL_SECONDS if self.busy() or self._admission_blocked else SWEEP_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            stop.cancel()
            wake.cancel()
        if self._lost:
            # Lost or released ownership: the durable row belongs to whoever
            # holds it next (a reconnecting launcher's new owner, or the
            # control plane), exactly as in _retire. Draining it here would
            # stop that successor as soon as it reads the row.
            return
        try:
            await begin_drain(self.incarnation_id, "stopped")
        except Exception:
            # Unreachable now; the runs still drain, and the control plane
            # sees the executor retire or fall silent.
            logger.exception("Pool worker could not record its drain")

    async def _claim_and_start(self) -> PoolClaim | None:
        """Atomically admit, canonically claim, reserve, and start one run.

        Subclasses may override this protected seam to use a different
        admission source, but must reserve the selected claim before leaving
        its admission context. ``claim_next`` invokes the reservation before
        its assignment transaction commits, so a dispatch never becomes
        visible as assigned while its host budget is unreserved.
        """
        assert self.admission is not None
        async with self.admission.claiming() as fits:
            # Nothing while over the memory threshold: work waits for an
            # executor with room, and the controller starts one if none has it.
            self._admission_blocked = fits is None
            if fits is None:
                return None
            claim = await claim_next(
                self.incarnation_id,
                self._claimable(),
                fits_bytes=fits,
                budget_bytes=self.admission.budget_bytes,
                reserve_claim=self._reserve_claim,
                release_reservation=self._release_claim_reservation,
            )
            if claim is not None:
                self._start(claim)
            return claim

    def _remember_started(self, *keys: str) -> None:
        now = time.monotonic()
        window = UNSTARTED_CLAIM_WINDOW.total_seconds()
        for key, at in list(self._started.items()):
            if now - at > window:
                del self._started[key]
        for key in keys:
            self._started[key] = now

    async def _release_unstarted_claims(self) -> None:
        """A claim can commit while its reply is lost: give back what this
        executor claimed but never started (see recovery.release_unstarted_claims)."""
        try:
            await release_unstarted_claims(self.incarnation_id, set(self._started))
        except Exception:
            # Still unreachable; the next failed claim tries again.
            logger.warning("Could not release unstarted claims", exc_info=True)

    def _reserve_claim(self, claim: PoolClaim) -> None:
        """Reserve a canonical claim before its assignment transaction commits."""
        assert self.admission is not None
        if claim.handler == "workflow.execute":
            self.admission.hold(str(claim.subject_id), claim.memory_request_bytes, workflow=True)
        else:
            self.admission.hold(str(claim.dispatch_id), claim.memory_request_bytes)

    def _release_claim_reservation(self, claim: PoolClaim) -> None:
        """Undo a reservation when its claim transaction or launch fails."""
        assert self.admission is not None
        if claim.handler == "workflow.execute":
            self.admission.release(str(claim.subject_id))
        else:
            self.admission.release(str(claim.dispatch_id))

    def _start(self, claim: PoolClaim) -> None:
        """Start a claim whose memory was already reserved transactionally."""
        coroutine = self._run_claim(claim)
        try:
            task = asyncio.create_task(coroutine, name=f"pool-run-{claim.dispatch_id}")
        except BaseException:
            coroutine.close()
            self._release_claim_reservation(claim)
            raise
        in_flight = self._tasks[claim.handler]
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)
        self._remember_started(str(claim.subject_id), str(claim.dispatch_id))

    async def _run_claim(self, claim: PoolClaim) -> None:
        assert self.admission is not None
        handed_back = False
        try:
            # Workflow runs return once routed to a child (then counted by the
            # pool); agent runs return when finished.
            if claim.handler == "workflow.execute":
                assert self._workflow_consumer is not None
                try:
                    await self._workflow_consumer.process_message(
                        claim.message,
                        PendingExecution(**claim.claimed)  # type: ignore[typeddict-item]
                        if claim.claimed is not None
                        else None,
                    )
                except MemoryError:
                    # Memory rose between the claim and the fork: the run
                    # goes back to waiting for an executor with room.
                    from src.services.execution.workflow_dispatch import release_workflow_run

                    self.admission.release(str(claim.subject_id))
                    handed_back = True
                    assert claim.claimed is not None
                    await release_workflow_run(
                        worker_id=self.incarnation_id,
                        message=claim.message,
                        payload=claim.claimed,
                        memory_request_bytes=claim.memory_request_bytes,
                        queued_at=claim.queued_at,
                    )
            elif claim.handler == "agent.run":
                assert self._agent_consumer is not None
                await self._agent_consumer.process_message(claim.message)
            else:
                await handle_summarize_message(claim.message)
        except Exception:
            logger.exception(
                "Pool run failed in its consumer",
                extra={"dispatch_id": str(claim.dispatch_id)},
            )
        finally:
            if claim.handler == "workflow.execute":
                # A routed workflow run stays held until its child ends. A run
                # handed back may already be claimed again (its own hold).
                if not handed_back:
                    self.admission.started(str(claim.subject_id))
            else:
                self.admission.release(str(claim.dispatch_id))
            self.runs_handled += 1

    async def _platform_job_loop(self) -> None:
        """Claim local platform jobs while their memory fits the free budget.

        Each claimed job runs in its own runner process and holds its memory
        requirement until it ends, sharing the budget with runs.
        """
        assert self.hosts_remote_work and self.admission is not None
        running: set[asyncio.Task[None]] = set()
        recovered_at: float | None = None
        while not self._platform_stop.is_set():
            claimed = False
            blocked = False
            self._platform_wake.clear()
            try:
                if recovered_at is None or time.monotonic() - recovered_at >= SWEEP_SECONDS:
                    await recover_expired_platform_jobs()
                    recovered_at = time.monotonic()
                claim = None
                async with self.admission.claiming() as fits:
                    blocked = fits is None
                    if fits is not None:
                        claim = await self._claim_platform_job(fits)
                        if claim is not None:
                            self.admission.hold(str(claim.id), claim.memory_required_bytes)
                if claim is not None:
                    claimed = True
                    self._platform_jobs.add(claim.id)
                    task = asyncio.create_task(
                        self._run_platform_job(claim), name=f"pool-platform-job-{claim.id}"
                    )
                    running.add(task)
                    task.add_done_callback(running.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Platform-job claim failed")
            if not claimed:
                stop = asyncio.ensure_future(self._platform_stop.wait())
                wake = asyncio.ensure_future(self._platform_wake.wait())
                await asyncio.wait(
                    {stop, wake},
                    timeout=PLATFORM_JOB_POLL_SECONDS if blocked or running else SWEEP_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                stop.cancel()
                wake.cancel()

    async def _run_platform_job(self, claim) -> None:  # type: ignore[no-untyped-def]
        try:
            await self._execute_platform_job(claim)
        except Exception:
            logger.exception("Platform job runner failed", extra={"job_id": str(claim.id)})
        finally:
            self._platform_jobs.discard(claim.id)
            assert self.admission is not None
            self.admission.release(str(claim.id))
            # Its memory, concurrency slot and resource lock are free.
            self._platform_wake.set()

    async def _wake_listener(self) -> None:
        """Turn acceptance hints into an immediate claim attempt."""
        from src.services.work_dispatch import listen_for_pool_wake

        await listen_for_pool_wake(
            self.settings.redis_url,
            self._wake,
            self._platform_wake,
            self._service_wake,
            self._package_wake,
        )

    async def _package_loop(self) -> None:
        """Apply every recorded package change newer than this executor's.

        Separate from the heartbeat, because pip can run for minutes. Each
        change is applied once, like the broadcast it replaces; a failure is
        reported through the shared install progress. It runs when a recorded
        change wakes the pool, and sweeps every SWEEP_SECONDS in case a
        wake-up was lost.
        """
        assert self.hosts_remote_work and self._package_consumer is not None
        while True:
            try:
                for change in await package_changes_after(self.package_generation):
                    await self._package_consumer.process_message(change_message(change))
                    self.package_generation = change.id
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pool worker package reconcile failed")
            # Timing out is the sweep; a wake-up ends the wait early.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._package_wake.wait(), SWEEP_SECONDS)
            # Cleared before the next read, so a change committed after this
            # point wakes another pass instead of being missed.
            self._package_wake.clear()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                # Bounded: a connection hung on a dead network must not keep
                # the next heartbeat from trying a fresh one.
                state = await asyncio.wait_for(heartbeat_worker(
                    self.incarnation_id,
                    active_runs=self.active_runs(),
                    runs_handled=self.runs_handled,
                    package_generation=self.package_generation,
                    memory_pressured=(
                        self.admission is not None and self.admission.pressured()
                    ),
                    memory_budget_bytes=(
                        self.budget.budget_bytes
                        if self._claim_ready and self.budget is not None
                        else None
                    ),
                    memory_reserved_bytes=(
                        0 if self.admission is None else self.admission.held_bytes()
                    ),
                ), timeout=HEARTBEAT_SECONDS)
                if state is None and not self._lost:
                    logger.error(
                        "This executor was declared lost; stopping its runs",
                        extra={"incarnation_id": str(self.incarnation_id)},
                    )
                    self._lost = True
                if state not in ("starting", "ready"):
                    # The controller drained or lost this worker: stop claiming.
                    self._stop.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pool worker heartbeat failed")
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def _start_service_claim_loop(self) -> None:
        """Supervise services in this executor's pool (separate service slots)."""
        from src.services.service_claim import ServiceClaimLoop

        assert self._workflow_consumer is not None and self.admission is not None
        pool = self._workflow_consumer._pool
        self._service_claim_loop = ServiceClaimLoop(
            worker_id=pool.worker_id,
            pool=pool,
            claim_interval_seconds=self.settings.service_claim_interval_seconds,
            beat_interval_seconds=self.settings.service_heartbeat_interval_seconds,
            lease_ttl_seconds=self.settings.service_lease_ttl_seconds,
            token_lifetime_seconds=self.settings.service_token_lifetime_seconds,
            claiming=self.admission.claiming,
            hold_memory=self._hold_service_memory,
            wake=self._service_wake,
        )
        pool.on_service_result = self._service_claim_loop.handle_service_result
        await self._service_claim_loop.start()

    async def _drain(self) -> None:
        """Hand services over, stop claiming platform jobs, let work finish.

        Services go first: they restart elsewhere through their lease.
        Runs and platform jobs then finish, each within its own timeout; a
        drain the control plane asked for (idle, rotated, max lifetime) has
        no deadline of its own. Once SIGTERM arrives (before or during the
        drain) they get ``DRAIN_GRACE_SECONDS``, and a workflow child still
        running at its end is recorded as interrupted by shutdown. An
        executor declared lost has no grace: its runs already ended.
        """
        if self._service_claim_loop is not None:
            try:
                await self._service_claim_loop.stop()
            except Exception:
                logger.exception("Pool worker service handover failed")
        self._platform_stop.set()
        # The heartbeat keeps running, so the control plane can still
        # declare this executor lost while it waits.
        while self._has_work() and not (self._terminating.is_set() or self._lost):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._terminating.wait(), timeout=0.5)
        if self._lost:
            # Their runs already ended; agent runs and summaries in progress
            # would only spend tokens on results nobody records.
            cancelled = [task for tasks in self._tasks.values() for task in tasks]
            for task in cancelled:
                task.cancel()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*cancelled, return_exceptions=True), timeout=5
                )
        deadline = time.monotonic() + (0.0 if self._lost else DRAIN_GRACE_SECONDS)
        while (
            any(self._tasks.values()) or self._platform_jobs
        ) and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        timed_out = [task for tasks in self._tasks.values() for task in tasks]
        if timed_out:
            # A graceful shutdown may wait out the full existing grace, but
            # an API-resident agent executor must not retire while its local
            # consumers still run. Cancellation lets each consumer's finally
            # path release admission and its canonical claim/recovery path
            # take over instead of leaving an orphaned task in the API.
            logger.warning("Pool worker drain grace expired; cancelling in-flight claims")
            for task in timed_out:
                task.cancel()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*timed_out, return_exceptions=True), timeout=5
                )
        if self._workflow_consumer is not None:
            await self._workflow_consumer._pool.drain_active_executions(
                max(0.0, deadline - time.monotonic())
            )

    def _has_work(self) -> bool:
        """Claims being routed, agent runs, summaries, workflow children or
        platform jobs still running."""
        return (
            any(self._tasks.values())
            or bool(self._platform_jobs)
            or (
                self._workflow_consumer is not None
                and self._workflow_consumer._pool.active_execution_count() > 0
            )
        )
