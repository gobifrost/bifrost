"""Host platform control inside an API replica's lifespan.

Enabled by ``BIFROST_CONTROL_PLANE_HOST=api``. Each API replica becomes a
control replica: it competes for the trigger lease and reconciles Kubernetes
builds, but never executes platform-job handlers. Startup does not wait for
control; HTTP serving begins immediately and control joins once the database
schema gate passes.

A control loop that exits unexpectedly stops control on this replica only. It
releases the trigger lease so another replica takes over, logs critically, and
stops publishing this replica to Diagnostics. HTTP serving continues.
"""

from __future__ import annotations

import asyncio
import logging

from shared.schema_gate import wait_for_schema
from src.core.database import get_engine
from src.scheduler.control import ControlPlane, replica_heartbeat_loop
from src.services.scheduler_diagnostics import remove_scheduler_replica

logger = logging.getLogger(__name__)

# API control replicas run no platform-job handlers.
API_CONTROL_JOB_SLOTS = 0


class ApiControlHost:
    def __init__(self, *, control_plane: ControlPlane | None = None) -> None:
        self.control = control_plane or ControlPlane(
            host="api",
            on_failure=self._control_failed,
        )
        self._stop_event = asyncio.Event()
        self._startup_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._failure_task: asyncio.Task[None] | None = None
        self._stopped = False

    async def start(self) -> None:
        self._startup_task = asyncio.create_task(
            self._start_after_schema(),
            name="api-control-startup",
        )
        self._startup_task.add_done_callback(self._startup_done)

    async def _start_after_schema(self) -> None:
        await wait_for_schema(get_engine())
        if self._stop_event.is_set():
            return
        self._heartbeat_task = asyncio.create_task(
            replica_heartbeat_loop(
                replica_id=self.control.owner_id,
                job_slots=API_CONTROL_JOB_SLOTS,
                stop_event=self._stop_event,
            ),
            name="api-control-heartbeat",
        )
        await self.control.start()

    def _startup_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or task.exception() is None:
            return
        logger.critical(
            "API platform control did not start",
            exc_info=task.exception(),
        )

    def _control_failed(self, task: asyncio.Task[None]) -> None:
        error = None if task.cancelled() else task.exception()
        logger.critical(
            "API platform control service %s stopped; this replica gives up control",
            task.get_name(),
            exc_info=error,
        )
        if self._failure_task is None:
            self._failure_task = asyncio.create_task(
                self.stop(),
                name="api-control-failure-stop",
            )

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop_event.set()
        if self._startup_task is not None and not self._startup_task.done():
            self._startup_task.cancel()
            await asyncio.gather(self._startup_task, return_exceptions=True)
        await self.control.stop()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
            try:
                await remove_scheduler_replica(self.control.owner_id)
            except Exception:
                logger.exception("Failed to remove API control replica heartbeat")


__all__ = ["API_CONTROL_JOB_SLOTS", "ApiControlHost"]
