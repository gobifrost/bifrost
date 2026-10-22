"""
Bifrost Scheduler - platform-job execution host and default control host.

Every replica runs a small durable platform-job pool. Local platform jobs run
in killable child processes that share this container's cgroup; PostgreSQL row
locking assigns each job to exactly one slot on one replica.

Platform control (scheduled-trigger leadership and Kubernetes build
reconciliation) lives in ``src.scheduler.control``. With the default
``BIFROST_CONTROL_PLANE_HOST=scheduler`` this process hosts it, as before. With
``api`` the API replicas host it and this process only executes local jobs.
"""

import asyncio
import logging
import signal
import sys

from src.config import get_settings
from shared.schema_gate import wait_for_schema
from src.core.database import close_db, get_engine, init_db
from src.jobs.schedulers.platform_jobs import platform_job_worker_loop
from src.scheduler.control import ControlPlane, replica_heartbeat_loop
from src.scheduler.health import (
    clear_ready,
    heartbeat_loop,
    mark_ready,
    write_heartbeat,
)
from src.scheduler.leadership import control_owner_id
from src.services.scheduler_diagnostics import remove_scheduler_replica


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Suppress noisy third-party loggers
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("aiobotocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

PLATFORM_JOB_CONCURRENCY = 2


class Scheduler:
    """Platform-job execution host, plus platform control when configured."""

    def __init__(self, *, control_plane: ControlPlane | None = None):
        self.settings = get_settings()
        self.running = False
        self._shutdown_event = asyncio.Event()
        if control_plane is not None:
            self._control: ControlPlane | None = control_plane
        elif self.settings.control_plane_host == "scheduler":
            self._control = ControlPlane(
                host="scheduler",
                on_failure=self._background_task_done,
            )
        else:
            self._control = None
        # Diagnostics marks the leader by matching this id to the lease owner.
        self.replica_id = (
            self._control.owner_id if self._control is not None else control_owner_id()
        )
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._diagnostics_task: asyncio.Task[None] | None = None
        self._job_slots = PLATFORM_JOB_CONCURRENCY
        self._platform_job_tasks: list[asyncio.Task[None]] = []

    @property
    def hosts_control(self) -> bool:
        return self._control is not None

    async def start(self) -> None:
        """Start the scheduler."""
        self.running = True
        logger.info("Starting Bifrost Scheduler...")
        logger.info(f"Environment: {self.settings.environment}")
        logger.info(
            "Platform control host: %s",
            "this scheduler" if self.hosts_control else "API replicas",
        )

        # Initialize database connection
        logger.info("Initializing database connection...")
        await init_db()
        logger.info("Database connection established")

        # Liveness (heartbeat) starts before the schema gate so a scheduler
        # waiting on migrations is alive but not ready.
        clear_ready()
        write_heartbeat()
        self._heartbeat_task = asyncio.create_task(heartbeat_loop())
        await wait_for_schema(get_engine())

        self._diagnostics_task = asyncio.create_task(
            replica_heartbeat_loop(
                replica_id=self.replica_id,
                job_slots=self._job_slots,
                stop_event=self._shutdown_event,
            ),
            name="scheduler-diagnostics-heartbeat",
        )
        self._platform_job_tasks = [
            asyncio.create_task(
                platform_job_worker_loop(self._shutdown_event),
                name=f"platform-job-worker-{slot + 1}",
            )
            for slot in range(self._job_slots)
        ]
        for task in self._platform_job_tasks:
            task.add_done_callback(self._background_task_done)
        if self._control is not None:
            await self._control.start()

        mark_ready()
        logger.info("Bifrost Scheduler replica started")
        logger.info("Running... (Ctrl+C to stop)")

        # Keep running until shutdown
        await self._shutdown_event.wait()

        control_tasks = self._control.tasks if self._control is not None else ()
        for task in (*self._platform_job_tasks, *control_tasks):
            if self.running and task.done() and not task.cancelled():
                error = task.exception()
                await self.stop()
                if error is not None:
                    raise RuntimeError(
                        f"Scheduler background task {task.get_name()} failed"
                    ) from error
                raise RuntimeError(
                    f"Scheduler background task {task.get_name()} stopped unexpectedly"
                )

    def _background_task_done(self, _task: asyncio.Task[None]) -> None:
        """Terminate the replica if an always-on loop exits."""
        if self.running and not self._shutdown_event.is_set():
            self._shutdown_event.set()

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if not self.running:
            return
        logger.info("Stopping Bifrost Scheduler...")
        self.running = False
        self._shutdown_event.set()

        # Surrender trigger leadership before stopping local execution.
        if self._control is not None:
            await self._control.stop()

        tasks = list(self._platform_job_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._platform_job_tasks = []

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None

        if self._diagnostics_task:
            self._diagnostics_task.cancel()
            await asyncio.gather(self._diagnostics_task, return_exceptions=True)
            self._diagnostics_task = None
        try:
            await remove_scheduler_replica(self.replica_id)
        except Exception:
            logger.exception("Failed to remove scheduler diagnostics heartbeat")

        # Close database connections
        await close_db()
        logger.info("Database connections closed")

        logger.info("Bifrost Scheduler stopped")

    def handle_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        asyncio.create_task(self.stop())


async def main() -> None:
    """Main entry point."""
    scheduler = Scheduler()

    # Register signal handlers
    def make_handler(s: signal.Signals) -> None:
        scheduler.handle_signal(int(s), None)

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, make_handler, signal.SIGINT)
    loop.add_signal_handler(signal.SIGTERM, make_handler, signal.SIGTERM)

    try:
        await scheduler.start()
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
