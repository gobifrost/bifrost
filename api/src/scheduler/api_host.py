"""Host platform control inside an API replica's lifespan.

Every API replica is a control replica: it competes for the trigger lease,
reconciles accepted work, and runs the Kubernetes loops when it may manage
Jobs, but never executes platform-job handlers. Startup does not wait for
control; HTTP serving begins immediately and control joins once the database
schema gate passes.

Control is supervised the way an orchestrator supervises the scheduler
process: if startup fails (schema gate timeout, database unavailable) or a
control loop exits, this replica stops control, releasing the trigger lease
so another replica can lead, logs critically, and starts control again after
``RESTART_BACKOFF_SECONDS``. HTTP serving is never interrupted.
"""

from __future__ import annotations

import asyncio
import logging

from shared.schema_gate import wait_for_schema
from src.core.database import get_engine
from src.scheduler.control import ControlPlane, replica_heartbeat_loop, wait_or_stop
from src.services.scheduler_diagnostics import remove_scheduler_replica

logger = logging.getLogger(__name__)

RESTART_BACKOFF_SECONDS = 30.0


class ApiControlHost:
    def __init__(
        self,
        *,
        control_plane: ControlPlane | None = None,
        restart_backoff_seconds: float = RESTART_BACKOFF_SECONDS,
    ) -> None:
        self._failed = asyncio.Event()
        self.control = control_plane or ControlPlane(on_failure=self._control_failed)
        self._restart_backoff_seconds = restart_backoff_seconds
        self._stop_event = asyncio.Event()
        self._supervisor: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._supervisor = asyncio.create_task(
            self._supervise(),
            name="api-control-supervisor",
        )

    def _control_failed(self, task: asyncio.Task[None]) -> None:
        error = None if task.cancelled() else task.exception()
        logger.critical(
            "API platform control service %s stopped; restarting control on this replica",
            task.get_name(),
            exc_info=error,
        )
        self._failed.set()

    async def _supervise(self) -> None:
        while not self._stop_event.is_set():
            self._failed.clear()
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.critical(
                    "API platform control could not start; retrying",
                    exc_info=True,
                )
            if self._stop_event.is_set():
                return
            await wait_or_stop(self._stop_event, self._restart_backoff_seconds)

    async def _wait_for_schema_or_stop(self) -> bool:
        """True once the schema gate passes; False if stop came first."""
        gate = asyncio.create_task(wait_for_schema(get_engine()))
        stop = asyncio.create_task(self._stop_event.wait())
        try:
            await asyncio.wait({gate, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
            if not gate.done():
                gate.cancel()
                await asyncio.gather(gate, return_exceptions=True)
        if self._stop_event.is_set():
            return False
        gate.result()
        return True

    async def _run_once(self) -> None:
        if not await self._wait_for_schema_or_stop():
            return
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            replica_heartbeat_loop(
                replica_id=self.control.owner_id,
                runs_platform_jobs=False,
                stop_event=heartbeat_stop,
            ),
            name="api-control-heartbeat",
        )
        try:
            await self.control.start()
            stop_waiter = asyncio.create_task(self._stop_event.wait())
            failure_waiter = asyncio.create_task(self._failed.wait())
            try:
                await asyncio.wait(
                    {stop_waiter, failure_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                stop_waiter.cancel()
                failure_waiter.cancel()
        finally:
            await self.control.stop()
            heartbeat_stop.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                await remove_scheduler_replica(self.control.owner_id)
            except Exception:
                logger.exception("Failed to remove API control replica heartbeat")

    async def stop(self) -> None:
        """Stop control and wait until the lease is released."""
        self._stop_event.set()
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is None:
            return
        try:
            await supervisor
        except Exception:
            logger.exception("API platform control supervisor failed")


__all__ = ["ApiControlHost", "RESTART_BACKOFF_SECONDS"]
