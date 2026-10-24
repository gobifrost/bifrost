"""Pool executor: claims runs from PostgreSQL and runs each in a fresh child.

Two forms share this loop. A *job* worker is started by the worker-pool
controller as one Kubernetes Job lifetime (it carries an incarnation). A
*static* worker is a long-running container (the ``scheduler`` and ``worker``
services, through their entry points) that registers itself and never idles
out. Every executor runs workflows, agent runs, summaries, services and
local platform jobs.

A job worker admits itself
to its incarnation (Job UID bound before start, pod UID bound once), then
claims pool dispatches from PostgreSQL and hands each to the ordinary
consumer code, which runs it in a fresh child of the process pool. With no
work for ``pool_idle_seconds`` (or past ``pool_max_lifetime_seconds``) it
moves itself to ``draining`` (after which it can never claim), lets active
runs finish, retires and exits 0. SIGTERM drains the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
import time
from collections.abc import Callable
from uuid import UUID

from shared.schema_gate import wait_for_schema
from src.config import Settings, get_settings
from src.core.database import close_db, get_engine, init_db
from src.core.redis_client import PendingExecution
from src.jobs.schedulers.platform_jobs import (
    claim_platform_job,
    recover_expired_platform_jobs,
    run_claimed_platform_job,
)
from src.scheduler.control import replica_heartbeat_loop
from src.services.kubernetes_control import start_kubernetes_loops
from src.services.scheduler_diagnostics import remove_scheduler_replica
from src.jobs.summarize_worker import handle_summarize_message
from src.services.memory_requests import (
    DEFAULT_WORKFLOW_REQUEST_BYTES,
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
    admit_worker,
    begin_drain,
    begin_idle_drain,
    claim_next,
    heartbeat_worker,
    register_static_worker,
    retire_worker,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 5.0
DRAIN_GRACE_SECONDS = 300.0
# How long the platform-job loop waits when nothing fits or is queued.
PLATFORM_JOB_IDLE_SECONDS = 2.0


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
        self.runs_handled = 0
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
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
        self._agent_consumer = None
        self._package_consumer = None
        self.package_generation = 0
        self._service_claim_loop = None
        # Measured once the executor is ready (src.services.memory_requests).
        self.budget: MemoryBudget | None = None
        # The memory gate every claim goes through (src.worker.admission):
        # workflow runs are held by execution id until their child ends,
        # other work by dispatch, platform job or service attempt id.
        self.admission: Admission | None = None
        # Ids of the local platform jobs this executor is running.
        self._platform_jobs: set[UUID] = set()
        # Set to stop claiming platform jobs; running ones finish.
        self._platform_stop = asyncio.Event()
        # The control plane declared this executor lost and ended its runs:
        # stop them instead of letting them finish (their results are refused).
        self._lost = False

    def request_stop(self) -> None:
        self._stop.set()

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
        handlers: list[tuple[str, int]] = [("workflow.execute", 1), ("agent.run", 1)]
        for summary_handler in ("agent.summarize", "agent.summarize_backfill"):
            if not self._tasks[summary_handler]:
                handlers.append((summary_handler, 1))
        return tuple(handlers)

    async def run(self) -> int:
        get_engine(executor_database_settings(self.settings))
        await init_db()
        await wait_for_schema(get_engine())
        from sqlalchemy.orm import configure_mappers

        configure_mappers()
        if self.static:
            early = measure_budget(self.settings.memory_pressure_threshold)
            slots: int | None = self._nominal_slots(early)
            self.incarnation_id = await register_static_worker(
                slots=slots, hostname=socket.gethostname(), memory_budget_bytes=early.budget_bytes
            )
            # Reported until the budget is measured with the pool up.
            self.budget = early
        else:
            assert self.job_uid is not None and self.pod_uid is not None
            slots = await admit_worker(
                self.incarnation_id, job_uid=self.job_uid, pod_uid=self.pod_uid
            )
        if slots is None:
            logger.error(
                "Pool worker was not admitted (not this Job's first pod)",
                extra={"incarnation_id": str(self.incarnation_id)},
            )
            return 1

        # Heartbeat from registration on: starting the pool (and installing
        # its requirements) can take minutes, longer than a silent executor
        # is given before it counts as lost.
        heartbeat = asyncio.create_task(self._heartbeat_loop(), name="pool-heartbeat")
        try:
            await self._start_executor()
        except BaseException:
            heartbeat.cancel()
            raise
        background = [
            heartbeat,
            asyncio.create_task(self._wake_listener(), name="pool-wake"),
            asyncio.create_task(self._package_loop(), name="pool-packages"),
            asyncio.create_task(
                replica_heartbeat_loop(
                    replica_id=str(self.incarnation_id),
                    runs_platform_jobs=True,
                    stop_event=self._platform_stop,
                ),
                name="pool-diagnostics-heartbeat",
            ),
            asyncio.create_task(self._platform_job_loop(), name="pool-platform-jobs"),
        ]
        if self.static:
            # On an install whose executor container may manage Jobs (the
            # builds runbook binds that Role to the scheduler), it runs the
            # Kubernetes loops; elsewhere this starts nothing.
            background.extend(await start_kubernetes_loops(self._platform_stop, self.settings))
        if self._on_ready is not None:
            self._on_ready()
        try:
            await self._claim_loop()
            await self._drain()
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await retire_worker(self.incarnation_id, runs_handled=self.runs_handled)
            try:
                await remove_scheduler_replica(str(self.incarnation_id))
            except Exception:
                logger.exception("Failed to remove the executor's diagnostics heartbeat")
            await self._workflow_consumer.stop_pool()
            await close_db()
        logger.info(
            "Pool worker retired",
            extra={"incarnation_id": str(self.incarnation_id), "runs": self.runs_handled},
        )
        return 0

    async def _start_executor(self) -> None:
        """Start the pool, consumers, memory gate and service supervision."""
        from src.jobs.consumers.agent_run import AgentRunConsumer
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        # Read the package generation before the pool installs requirements:
        # a change committed in between is then applied again (installs are
        # idempotent) instead of being missed.
        self.package_generation = await current_package_generation()
        self._workflow_consumer = WorkflowExecutionConsumer()
        self._workflow_consumer.owner_worker_id = self.incarnation_id
        await self._workflow_consumer.start_pool()
        self._agent_consumer = AgentRunConsumer()
        # Applies recorded package changes; it never consumes the broadcast.
        from src.jobs.consumers.package_install import PackageInstallConsumer

        self._package_consumer = PackageInstallConsumer()
        # Measured once the parent and fork template are up, before any
        # service claims: what remains is what work may use.
        self.budget = measure_budget(self.settings.memory_pressure_threshold)
        pool = self._workflow_consumer._pool
        pool.memory_kill_growth_bytes = max(
            0, self.budget.budget_bytes - WORKFLOW_REQUEST_MARGIN_BYTES
        )
        threshold = self.settings.memory_pressure_threshold
        self.admission = Admission(
            budget_bytes=self.budget.budget_bytes,
            under_pressure=lambda: under_pressure(threshold),
            running_workflow_ids=pool.active_execution_ids,
            workflow_growth=pool.workflow_growth,
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
                claim = None
                async with self.admission.claiming() as fits:
                    # Nothing while over the memory threshold: the work waits for
                    # an executor with room, and the controller starts one if
                    # none has it.
                    if fits is not None:
                        claim = await claim_next(
                            self.incarnation_id,
                            self._claimable(),
                            fits_bytes=fits,
                            budget_bytes=self.admission.budget_bytes,
                        )
                        if claim is not None:
                            self._start(claim)
                if claim is not None:
                    last_activity = time.monotonic()
                    continue
                if self.busy():
                    last_activity = time.monotonic()
                elif (
                    not self.static
                    and time.monotonic() - last_activity >= self.settings.pool_idle_seconds
                ):
                    if await begin_idle_drain(
                        self.incarnation_id, keep_ready=self.settings.pool_min_workers
                    ):
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
                continue
            stop = asyncio.ensure_future(self._stop.wait())
            wake = asyncio.ensure_future(self._wake.wait())
            await asyncio.wait({stop, wake}, timeout=POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel()
            wake.cancel()
        try:
            await begin_drain(self.incarnation_id, "stopped")
        except Exception:
            # Unreachable now; the runs still drain, and the control plane
            # sees the executor retire or fall silent.
            logger.exception("Pool worker could not record its drain")

    @staticmethod
    def _nominal_slots(budget: MemoryBudget) -> int:
        """Typical runs the budget fits. Only an older worker controller
        reads slots (during a rolling upgrade); capacity is the budget."""
        return max(1, budget.budget_bytes // DEFAULT_WORKFLOW_REQUEST_BYTES)

    def _start(self, claim: PoolClaim) -> None:
        assert self.admission is not None
        if claim.handler == "workflow.execute":
            self.admission.hold(str(claim.subject_id), claim.memory_request_bytes, workflow=True)
        else:
            self.admission.hold(str(claim.dispatch_id), claim.memory_request_bytes)
        task = asyncio.create_task(self._run_claim(claim), name=f"pool-run-{claim.dispatch_id}")
        in_flight = self._tasks[claim.handler]
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)

    async def _run_claim(self, claim: PoolClaim) -> None:
        assert self._workflow_consumer is not None and self._agent_consumer is not None
        assert self.admission is not None
        handed_back = False
        try:
            # Workflow runs return once routed to a child (then counted by the
            # pool); agent runs return when finished.
            if claim.handler == "workflow.execute":
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
        assert self.admission is not None
        running: set[asyncio.Task[None]] = set()
        while not self._platform_stop.is_set():
            claimed = False
            try:
                await recover_expired_platform_jobs()
                claim = None
                async with self.admission.claiming() as fits:
                    if fits is not None:
                        claim = await claim_platform_job(backend="local", fits_bytes=fits)
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
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._platform_stop.wait(), timeout=PLATFORM_JOB_IDLE_SECONDS
                    )

    async def _run_platform_job(self, claim) -> None:  # type: ignore[no-untyped-def]
        try:
            await run_claimed_platform_job(claim)
        except Exception:
            logger.exception("Platform job runner failed", extra={"job_id": str(claim.id)})
        finally:
            self._platform_jobs.discard(claim.id)
            assert self.admission is not None
            self.admission.release(str(claim.id))

    async def _wake_listener(self) -> None:
        """Turn acceptance hints into an immediate claim attempt."""
        import redis.asyncio as aioredis

        from src.services.work_dispatch import POOL_WAKE_CHANNEL

        while True:
            client = aioredis.from_url(self.settings.redis_url)
            try:
                pubsub = client.pubsub()
                await pubsub.subscribe(POOL_WAKE_CHANNEL)
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        self._wake.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Hints are an optimization; polling continues meanwhile.
                logger.warning("Pool wake listener reconnecting", exc_info=True)
                await asyncio.sleep(HEARTBEAT_SECONDS)
            finally:
                await client.aclose()

    async def _package_loop(self) -> None:
        """Apply every recorded package change newer than this executor's.

        Separate from the heartbeat, because pip can run for minutes. Each
        change is applied once, like the broadcast it replaces; a failure is
        reported through the shared install progress.
        """
        assert self._package_consumer is not None
        while True:
            try:
                for change in await package_changes_after(self.package_generation):
                    await self._package_consumer.process_message(change_message(change))
                    self.package_generation = change.id
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pool worker package reconcile failed")
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                state = await heartbeat_worker(
                    self.incarnation_id,
                    active_runs=self.active_runs(),
                    runs_handled=self.runs_handled,
                    package_generation=self.package_generation,
                    memory_pressured=(
                        self.admission is not None and self.admission.pressured()
                    ),
                    memory_budget_bytes=None if self.budget is None else self.budget.budget_bytes,
                    memory_reserved_bytes=(
                        0 if self.admission is None else self.admission.held_bytes()
                    ),
                )
                if state is None and not self._lost:
                    logger.error(
                        "This executor was declared lost; stopping its runs",
                        extra={"incarnation_id": str(self.incarnation_id)},
                    )
                    self._lost = True
                if state != "ready":
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
        )
        pool.on_service_result = self._service_claim_loop.handle_service_result
        await self._service_claim_loop.start()

    async def _drain(self) -> None:
        """Hand services over, stop claiming platform jobs, let work finish.

        Services go first: they restart elsewhere through their lease.
        Running runs and platform jobs finish within the grace; a workflow
        child still running at its end is recorded as interrupted by shutdown.
        An executor declared lost has no grace: its runs already ended.
        """
        if self._service_claim_loop is not None:
            try:
                await self._service_claim_loop.stop()
            except Exception:
                logger.exception("Pool worker service handover failed")
        self._platform_stop.set()
        if self._lost:
            # Their runs already ended; agent runs and summaries in progress
            # would only spend tokens on results nobody records.
            for tasks in self._tasks.values():
                for task in tasks:
                    task.cancel()
        deadline = time.monotonic() + (0.0 if self._lost else DRAIN_GRACE_SECONDS)
        while (
            any(self._tasks.values()) or self._platform_jobs
        ) and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        if self._workflow_consumer is not None:
            await self._workflow_consumer._pool.drain_active_executions(
                max(0.0, deadline - time.monotonic())
            )


async def run_executor(*, on_ready: Callable[[], None] | None = None) -> int:
    """Run this process's executor until it is stopped or drains.

    An executor is a Job worker when the controller gave it an incarnation;
    otherwise it is static (the scheduler and worker containers).
    """
    incarnation = os.environ.get("BIFROST_WORKER_INCARNATION_ID")
    if incarnation:
        job_uid = os.environ.get("BIFROST_KUBERNETES_JOB_UID")
        pod_uid = os.environ.get("BIFROST_KUBERNETES_POD_UID")
        if not job_uid or not pod_uid:
            logger.error("Pool worker identity is missing from its environment")
            return 1
        worker = PoolWorker(
            incarnation_id=UUID(incarnation), job_uid=job_uid, pod_uid=pod_uid, on_ready=on_ready
        )
    else:
        worker = PoolWorker(on_ready=on_ready)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)
    return await worker.run()


if __name__ == "__main__":
    sys.exit(asyncio.run(run_executor()))
