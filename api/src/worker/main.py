"""
Bifrost Worker - Background Worker Service

Main entry point for the background job worker.
Handles RabbitMQ message consumption for workflow execution and package installation.

This container is responsible for:
- Consuming workflow execution messages from RabbitMQ
- Executing workflow code (with thread pool for blocking code)
- Pushing results to Redis for sync execution requests
- Package installation

Can be scaled horizontally (replicas: N) for increased throughput.
"""

import asyncio
import logging
import os
import signal

from src.config import get_settings
from src.core.database import init_db, close_db
from src.jobs.rabbitmq import rabbitmq
from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.jobs.consumers.package_install import PackageInstallConsumer
from src.jobs.consumers.agent_run import AgentRunConsumer
from src.jobs.summarize_worker import (
    SummarizeBackfillConsumer,
    SummarizeConsumer,
    TuneChatConsumer,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Suppress noisy third-party loggers
logging.getLogger("aiormq").setLevel(logging.WARNING)
logging.getLogger("aio_pika").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("aiobotocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Enable DEBUG for execution engine to troubleshoot workflows
logging.getLogger("src.services.execution").setLevel(logging.DEBUG)
logging.getLogger("bifrost").setLevel(logging.DEBUG)
logging.getLogger("src.jobs.consumers.workflow_execution").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


class Worker:
    """
    Background jobs worker.

    Manages RabbitMQ consumers for:
    - Workflow execution (with Redis result push for sync requests)
    - Package installation
    """

    def __init__(self):
        self.settings = get_settings()
        self.running = False
        self._shutdown_event = asyncio.Event()
        self._consumers: list = []
        self._stopping = False

    async def start(self) -> None:
        """Start the worker.

        On any startup failure, fully tear down whatever has been started
        so the process can exit cleanly. Without this, a partially-started
        worker leaks its process pool's template child process, which in
        turn blocks Python's multiprocessing atexit cleanup inside
        waitpid() — leaving PID 1 hung forever and the container looking
        healthy to Kubernetes even though the worker is dead.
        """
        self.running = True
        logger.info("Starting Bifrost Worker...")
        logger.info(f"Environment: {self.settings.environment}")

        try:
            # Initialize database connection
            logger.info("Initializing database connection...")
            await init_db()
            logger.info("Database connection established")

            # Initialize and start RabbitMQ consumers
            logger.info("Starting RabbitMQ consumers...")
            await self._start_consumers()
        except Exception:
            logger.error("Startup failed; tearing down partially-started worker")
            await self._cleanup_after_failed_start()
            raise

        logger.info("Bifrost Worker started")
        logger.info("Waiting for messages... (Ctrl+C to stop)")

        # Keep running until shutdown
        await self._shutdown_event.wait()

    async def _cleanup_after_failed_start(self) -> None:
        """Best-effort teardown of any resources started before a failure.

        Called when start() raises partway through. Must be tolerant of
        consumers that never got past __init__, and of consumers whose
        own stop() might also fail.
        """
        for consumer in self._consumers:
            try:
                await consumer.stop()
            except Exception as e:
                logger.error(
                    f"Error stopping consumer {consumer.queue_name} during cleanup: {e}"
                )

        try:
            await rabbitmq.close()
        except Exception as e:
            logger.error(f"Error closing RabbitMQ pools during cleanup: {e}")

        try:
            await close_db()
        except Exception as e:
            logger.error(f"Error closing DB during cleanup: {e}")

    async def _start_consumers(self) -> None:
        """Start all RabbitMQ consumers."""
        # Create consumer instances
        self._consumers = [
            WorkflowExecutionConsumer(),
            PackageInstallConsumer(),
            AgentRunConsumer(),
            SummarizeConsumer(),
            SummarizeBackfillConsumer(),
            TuneChatConsumer(),
        ]

        # Start each consumer
        for consumer in self._consumers:
            try:
                await consumer.start()
                logger.info(f"Started consumer: {consumer.queue_name}")
            except Exception as e:
                logger.error(f"Failed to start consumer {consumer.queue_name}: {e}")
                raise

    async def stop(self) -> None:
        """Stop the worker gracefully (drain in-flight, then close).

        Each consumer cancels its consumer tag (so RabbitMQ stops handing
        it new messages), waits for in-flight tasks to finish (up to the
        drain deadline), then closes its channel. The deadline is tunable
        via BIFROST_DRAIN_DEADLINE_SECONDS (default 300s) and should be
        less than the K8s terminationGracePeriodSeconds with margin for
        connection cleanup.
        """
        if self._stopping:
            return
        self._stopping = True
        logger.info("Stopping Bifrost Worker (graceful drain)...")
        self.running = False

        # Drain consumers in parallel — each cancels its consumer tag, waits on
        # its in-flight tasks, then closes its channel.
        deadline_str = os.environ.get("BIFROST_DRAIN_DEADLINE_SECONDS", "300")
        try:
            drain_deadline = float(deadline_str)
            if drain_deadline <= 0:
                raise ValueError(f"must be positive, got {drain_deadline}")
        except ValueError as e:
            logger.warning(
                f"Invalid BIFROST_DRAIN_DEADLINE_SECONDS={deadline_str!r}: {e}; "
                f"falling back to 300s"
            )
            drain_deadline = 300.0
        results = await asyncio.gather(
            *(self._drain_consumer(consumer, drain_deadline) for consumer in self._consumers),
            return_exceptions=True,
        )
        for consumer, result in zip(self._consumers, results):
            if isinstance(result, Exception):
                logger.error(f"Error draining consumer {consumer.queue_name}: {result}")
            else:
                logger.info(f"Drained consumer: {consumer.queue_name}")

        # Close RabbitMQ pools (idempotent if already closed).
        await rabbitmq.close()
        logger.info("RabbitMQ connections closed")

        # Close database connections.
        await close_db()
        logger.info("Database connections closed")

        self._shutdown_event.set()
        logger.info("Bifrost Worker stopped")

    async def _drain_consumer(self, consumer, deadline: float) -> None:
        """Helper: drain one consumer with the given deadline."""
        await consumer.drain(deadline=deadline)

    def handle_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        asyncio.create_task(self.stop())


async def main() -> None:
    """Main entry point."""
    worker = Worker()

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: worker.handle_signal(s, None))

    try:
        await worker.start()
    except Exception as e:
        logger.error(f"Worker error: {e}", exc_info=True)
        # Hard-exit bypassing atexit handlers. Python's multiprocessing
        # atexit will otherwise block in waitpid() if any subprocess is
        # still running (e.g., a template child that cleanup failed to
        # stop), leaving PID 1 hung and the container "healthy" to k8s
        # despite the worker being dead. Force exit here so kubelet sees
        # the container terminate and restarts it.
        os._exit(1)


if __name__ == "__main__":
    asyncio.run(main())
