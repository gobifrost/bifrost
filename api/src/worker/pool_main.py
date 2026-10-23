"""Pool executor: claims runs from PostgreSQL and runs each in a fresh child.

Two forms share this loop. A *job* worker is started by the control plane's
worker-pool controller as one Kubernetes Job lifetime. A *static* worker
(``BIFROST_WORKER_FORM=static``) is a long-running Deployment or Compose
container that registers itself and never idles out.

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
import logging
import os
import signal
import socket
import sys
import time
from uuid import UUID

from shared.schema_gate import wait_for_schema
from src.config import Settings, get_settings
from src.core.database import close_db, get_engine, init_db
from src.core.redis_client import PendingExecution
from src.jobs.schedulers.platform_jobs import platform_job_worker_loop
from src.scheduler.control import replica_heartbeat_loop
from src.services.scheduler_diagnostics import remove_scheduler_replica
from src.jobs.summarize_worker import handle_summarize_message
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
logging.getLogger("aiormq").setLevel(logging.WARNING)
logging.getLogger("aio_pika").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 5.0
DRAIN_GRACE_SECONDS = 300.0
# Local platform jobs run one at a time per slot, as on a scheduler replica.
PLATFORM_JOB_SLOTS = 2


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
    ) -> None:
        """A job worker passes its incarnation and UIDs; a static one, none."""
        self.settings = get_settings()
        self.static = incarnation_id is None
        self.incarnation_id: UUID = incarnation_id or UUID(int=0)
        self.job_uid = job_uid
        self.pod_uid = pod_uid
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
        # Ids of the local platform jobs this executor is running.
        self._platform_jobs: set[UUID] = set()
        # Set to stop claiming platform jobs; running ones finish.
        self._platform_stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def _workflow_runs(self) -> int:
        pool_runs = (
            self._workflow_consumer._pool.active_execution_count()
            if self._workflow_consumer is not None
            else 0
        )
        return pool_runs + len(self._tasks["workflow.execute"])

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

    def _claimable(self) -> tuple[tuple[str, int], ...]:
        """Handlers with free capacity, under the broker worker's limits:
        process-pool forks for workflow runs (``max_workers``), concurrent
        agent runs (``max_concurrency``), and one live summary plus one
        backfill summary at a time (the summary consumers' prefetch of 1, so
        a large backfill never delays live summaries)."""
        handlers: list[tuple[str, int]] = []
        if self._workflow_runs() < self.settings.max_workers:
            handlers.append(("workflow.execute", 1))
        if len(self._tasks["agent.run"]) < self.settings.max_concurrency:
            handlers.append(("agent.run", 1))
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
            slots: int | None = self.settings.max_workers
            self.incarnation_id = await register_static_worker(
                slots=self.settings.max_workers, hostname=socket.gethostname()
            )
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

        from src.jobs.consumers.agent_run import AgentRunConsumer
        from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

        # Read the package generation before the pool installs requirements:
        # a change committed in between is then applied again (installs are
        # idempotent) instead of being missed.
        self.package_generation = await current_package_generation()
        self._workflow_consumer = WorkflowExecutionConsumer()
        await self._workflow_consumer.start_pool()
        self._agent_consumer = AgentRunConsumer()
        # Applies recorded package changes; it never consumes the broadcast.
        from src.jobs.consumers.package_install import PackageInstallConsumer

        self._package_consumer = PackageInstallConsumer()
        await self._start_service_claim_loop()
        logger.info(
            "Pool worker ready",
            extra={"incarnation_id": str(self.incarnation_id), "slots": slots},
        )
        background = [
            asyncio.create_task(self._heartbeat_loop(), name="pool-heartbeat"),
            asyncio.create_task(self._wake_listener(), name="pool-wake"),
            asyncio.create_task(self._package_loop(), name="pool-packages"),
            asyncio.create_task(
                replica_heartbeat_loop(
                    replica_id=str(self.incarnation_id),
                    job_slots=PLATFORM_JOB_SLOTS,
                    stop_event=self._platform_stop,
                ),
                name="pool-diagnostics-heartbeat",
            ),
            *(
                asyncio.create_task(
                    platform_job_worker_loop(self._platform_stop, active=self._platform_jobs),
                    name=f"pool-platform-job-{slot + 1}",
                )
                for slot in range(PLATFORM_JOB_SLOTS)
            ),
        ]
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
            from src.jobs.rabbitmq import rabbitmq

            await rabbitmq.close()
            await close_db()
        logger.info(
            "Pool worker retired",
            extra={"incarnation_id": str(self.incarnation_id), "runs": self.runs_handled},
        )
        return 0

    async def _claim_loop(self) -> None:
        started = time.monotonic()
        last_activity = time.monotonic()
        while not self._stop.is_set():
            # Clear before looking, so a hint that lands during the claim
            # attempt still wakes the wait below instead of being lost.
            self._wake.clear()
            handlers = self._claimable()
            if handlers:
                claim = await claim_next(self.incarnation_id, handlers)
                if claim is not None:
                    self._start(claim)
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
            stop = asyncio.ensure_future(self._stop.wait())
            wake = asyncio.ensure_future(self._wake.wait())
            await asyncio.wait({stop, wake}, timeout=POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel()
            wake.cancel()
        await begin_drain(self.incarnation_id, "stopped")

    def _start(self, claim: PoolClaim) -> None:
        task = asyncio.create_task(self._run_claim(claim), name=f"pool-run-{claim.dispatch_id}")
        in_flight = self._tasks[claim.handler]
        in_flight.add(task)
        task.add_done_callback(in_flight.discard)

    async def _run_claim(self, claim: PoolClaim) -> None:
        assert self._workflow_consumer is not None and self._agent_consumer is not None
        try:
            # Workflow runs return once routed to a child (then counted by the
            # pool); agent runs return when finished.
            if claim.handler == "workflow.execute":
                await self._workflow_consumer.process_message(
                    claim.message,
                    claimed=PendingExecution(**claim.claimed)  # type: ignore[typeddict-item]
                    if claim.claimed is not None
                    else None,
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
            self.runs_handled += 1

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
                )
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

        assert self._workflow_consumer is not None
        pool = self._workflow_consumer._pool
        self._service_claim_loop = ServiceClaimLoop(
            worker_id=pool.worker_id,
            pool=pool,
            claim_interval_seconds=self.settings.service_claim_interval_seconds,
            beat_interval_seconds=self.settings.service_heartbeat_interval_seconds,
            lease_ttl_seconds=self.settings.service_lease_ttl_seconds,
            token_lifetime_seconds=self.settings.service_token_lifetime_seconds,
        )
        pool.on_service_result = self._service_claim_loop.handle_service_result
        await self._service_claim_loop.start()

    async def _drain(self) -> None:
        """Hand services over, stop claiming platform jobs, let work finish.

        Services go first, like the broker worker's stop: they restart
        elsewhere through their lease. Running runs and platform jobs finish.
        """
        if self._service_claim_loop is not None:
            try:
                await self._service_claim_loop.stop()
            except Exception:
                logger.exception("Pool worker service handover failed")
        self._platform_stop.set()
        deadline = time.monotonic() + DRAIN_GRACE_SECONDS
        while (self.active_runs() > 0 or self._platform_jobs) and time.monotonic() < deadline:
            await asyncio.sleep(0.5)


async def main() -> int:
    if os.environ.get("BIFROST_WORKER_FORM") == "static":
        worker = PoolWorker()
    else:
        incarnation = os.environ.get("BIFROST_WORKER_INCARNATION_ID")
        job_uid = os.environ.get("BIFROST_KUBERNETES_JOB_UID")
        pod_uid = os.environ.get("BIFROST_KUBERNETES_POD_UID")
        if not incarnation or not job_uid or not pod_uid:
            logger.error("Pool worker identity is missing from its environment")
            return 1
        worker = PoolWorker(
            incarnation_id=UUID(incarnation), job_uid=job_uid, pod_uid=pod_uid
        )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)
    return await worker.run()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
