"""RabbitMQ consumer for durable Solution deploy/install jobs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from uuid import UUID

from src.core.database import get_db_context
from src.jobs.rabbitmq import BaseConsumer, publish_message
from src.services.solutions.deploy_jobs import (
    DEPLOY_QUEUE,
    execute_deploy_job,
    mark_deploy_job_published,
    recover_deploy_jobs,
)

logger = logging.getLogger(__name__)
_RECOVERY_INTERVAL_SECONDS = 30.0


class SolutionDeployConsumer(BaseConsumer):
    def __init__(self) -> None:
        # One heavy reconcile/finalize at a time per worker. Horizontal worker
        # scaling still increases throughput and per-install locks serialize
        # conflicting jobs.
        super().__init__(queue_name=DEPLOY_QUEUE, prefetch_count=1)
        self._recovery_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await super().start()
        await self._recover()
        self._recovery_task = asyncio.create_task(self._recovery_loop())

    async def _recover(self) -> None:
        async with get_db_context() as db:
            job_ids = await recover_deploy_jobs(db)
        for job_id in job_ids:
            await publish_message(DEPLOY_QUEUE, {"job_id": str(job_id)})
            await mark_deploy_job_published(job_id)
        if job_ids:
            logger.info("Requeued %d durable Solution deploy job(s)", len(job_ids))

    async def _recovery_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_RECOVERY_INTERVAL_SECONDS)
                await self._recover()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - durable retry loop must continue
                logger.exception("Solution deploy job recovery pass failed")

    async def _stop_recovery(self) -> None:
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recovery_task
            self._recovery_task = None

    async def drain(self, deadline: float = 300.0) -> None:
        await self._stop_recovery()
        await super().drain(deadline=deadline)

    async def stop(self) -> None:
        await self._stop_recovery()
        await super().stop()

    async def process_message(self, body: dict) -> None:
        # Queue payload is intentionally capability-free: all parameters and
        # staged locations are loaded from the database by exact job id.
        if set(body) != {"job_id"}:
            raise ValueError("Solution deploy queue message must contain only job_id")
        await execute_deploy_job(UUID(str(body["job_id"])))
