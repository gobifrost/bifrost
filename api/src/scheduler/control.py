"""Platform control services, hosted by every API replica.

The control plane owns two kinds of work:

- **Trigger leadership.** Replicas compete for the fenced ``scheduler-triggers``
  lease. Only the holder runs the code-owned schedule catalog. Every callback
  re-checks the conservative local lease deadline before it starts, so a
  replica that stops renewing stops producing triggers before PostgreSQL can
  elect a successor. The lease name is the one older schedulers used: during a
  rollout an old scheduler and a new API replica can never both lead.
- **Kubernetes control.** The worker controller and the build-Job controller
  start wherever this process may manage Jobs (``kubernetes_control``); every
  permitted replica reconciles concurrently. Row locks, the backend advisory
  lock, deterministic Job names and UID binding make concurrent reconcilers
  launch each attempt once.

Execution stays outside this service: local platform jobs, workflows,
agent runs and services run on the executors.
"""

from __future__ import annotations

import asyncio
import time
import importlib
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config import Settings, get_settings
from src.scheduler.leadership import SchedulerLeadershipLease
from src.scheduler.registry import (
    SCHEDULED_TASKS,
    ScheduledTaskDefinition,
    ScheduledTaskOutcome,
)
from src.services.scheduler_diagnostics import (
    finish_scheduler_run,
    heartbeat_scheduler_replica,
    publish_task_states,
    start_scheduler_run,
)

logger = logging.getLogger(__name__)

LEADERSHIP_RETRY_SECONDS = 5.0
LEADERSHIP_RENEW_SECONDS = 10.0
REPLICA_HEARTBEAT_SECONDS = 10.0
# The dispatch reconciler runs on a pool wake-up (new work, or work released),
# at most every DISPATCH_RECONCILE_SECONDS, and sweeps every
# DISPATCH_SWEEP_SECONDS when nothing wakes it.
DISPATCH_RECONCILE_SECONDS = 5.0
DISPATCH_SWEEP_SECONDS = 15.0
# A new leader spreads its run-at-startup callbacks this far apart so the
# burst does not take a host's whole shared connection pool at once.
# Recurring runs are not throttled: a waiting job would count as a running
# instance and APScheduler would skip its next firing.
STARTUP_STAGGER_SECONDS = 0.5
MISFIRE_OPTIONS: dict[str, Any] = {
    "misfire_grace_time": 60 * 10,
    "coalesce": True,
}

FailureCallback = Callable[["asyncio.Task[None]"], None]


def resolve_callback(reference: str) -> Callable[[], Awaitable[Any]]:
    module_name, _, attribute = reference.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def build_trigger(definition: ScheduledTaskDefinition) -> CronTrigger | IntervalTrigger:
    if definition.trigger == "cron":
        return CronTrigger(timezone=timezone.utc, **definition.trigger_args)
    return IntervalTrigger(timezone=timezone.utc, **definition.trigger_args)  # type: ignore[arg-type]


async def wait_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        # The interval elapsed without a stop request.
        return


async def replica_heartbeat_loop(
    *,
    replica_id: str,
    runs_platform_jobs: bool,
    stop_event: asyncio.Event,
) -> None:
    """Publish this process to Diagnostics until stopped."""
    started_at = datetime.now(timezone.utc)
    while not stop_event.is_set():
        try:
            await heartbeat_scheduler_replica(
                replica_id=replica_id,
                hostname=socket.gethostname(),
                pid=os.getpid(),
                runs_platform_jobs=runs_platform_jobs,
                started_at=started_at,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to publish control replica heartbeat")
        await wait_or_stop(stop_event, REPLICA_HEARTBEAT_SECONDS)


class ControlPlane:
    """Lifecycle-managed trigger leadership and build reconciliation."""

    def __init__(
        self,
        *,
        leadership_lease: SchedulerLeadershipLease | None = None,
        settings: Settings | None = None,
        catalog: tuple[ScheduledTaskDefinition, ...] = SCHEDULED_TASKS,
        on_failure: FailureCallback | None = None,
        renew_seconds: float = LEADERSHIP_RENEW_SECONDS,
        retry_seconds: float = LEADERSHIP_RETRY_SECONDS,
    ) -> None:
        self._renew_seconds = renew_seconds
        self._retry_seconds = retry_seconds
        self.settings = settings or get_settings()
        self.lease = leadership_lease or SchedulerLeadershipLease()
        self._catalog = catalog
        self._on_failure = on_failure
        self._stop_event = asyncio.Event()
        self._scheduler: AsyncIOScheduler | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.running = False

    @property
    def owner_id(self) -> str:
        return self.lease.owner_id

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._tasks)

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(self._leadership_loop(), name="control-leadership"),
            asyncio.create_task(
                self._dispatch_reconcile_loop(), name="work-dispatch-reconciler"
            ),
        ]
        from src.core.database import get_db_context
        from src.services.kubernetes_control import start_kubernetes_loops
        from src.services.kubernetes_execution import import_kubernetes_policy_from_env

        # Settings never stored take their deployment variables before any
        # controller runs a pass on defaults.
        async with get_db_context() as db:
            await import_kubernetes_policy_from_env(db, os.environ)
        self._tasks.extend(await start_kubernetes_loops(self._stop_event, self.settings))
        for task in self._tasks:
            task.add_done_callback(self._task_done)
        logger.info(
            "Platform control started",
            extra={"owner_id": self.owner_id},
        )

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """Report an always-on control loop that exited outside stop()."""
        if self.running and not self._stop_event.is_set() and self._on_failure:
            self._on_failure(task)

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(
            "Platform control stopped",
            extra={"owner_id": self.owner_id},
        )

    async def _dispatch_reconcile_loop(self) -> None:
        """End silent static executors, settle accepted work no executor holds
        or claimed in time, and fail work no executor can ever hold."""
        from src.config import get_settings
        from src.services.work_dispatch import (
            listen_for_pool_wake,
            reconcile_work_dispatches,
            wait_for_next_pass,
        )
        from src.services.worker_pool.liveness import Reachability, end_silent_static_executors
        from src.services.worker_pool.unfit import settle_work_that_never_fits

        reachability = Reachability()
        wake = asyncio.Event()
        listener = asyncio.create_task(
            listen_for_pool_wake(get_settings().redis_url, wake), name="dispatch-reconciler-wake"
        )
        try:
            while not self._stop_event.is_set():
                started = time.monotonic()
                try:
                    if await reachability.check():
                        if reachability.steady():
                            await end_silent_static_executors()
                        await reconcile_work_dispatches()
                        await settle_work_that_never_fits()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Work dispatch reconciliation failed")
                await wait_for_next_pass(
                    self._stop_event, wake, since=started,
                    spacing=DISPATCH_RECONCILE_SECONDS, sweep=DISPATCH_SWEEP_SECONDS,
                )
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

    async def _run_scheduled_task(
        self,
        task_id: str,
        callback: Callable[[], Awaitable[Any]],
    ) -> None:
        # Fence at start: a replica whose lease may have been handed over
        # must not start another trigger.
        if not self.lease.holds_lease():
            logger.warning(
                "Skipped scheduled task without a current trigger lease",
                extra={"task_id": task_id, "owner_id": self.owner_id},
            )
            return
        run_id = await start_scheduler_run(task_id, self.owner_id)
        try:
            result = await callback()
        except asyncio.CancelledError:
            await finish_scheduler_run(
                run_id,
                status="failed",
                error_message="Scheduler leadership ended while the task was running.",
            )
            raise
        except Exception as exc:
            await finish_scheduler_run(
                run_id,
                status="failed",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise
        outcome = (
            result
            if isinstance(result, ScheduledTaskOutcome)
            else ScheduledTaskOutcome(summary="Completed")
        )
        await finish_scheduler_run(
            run_id,
            status="enqueued" if outcome.platform_job_id else "succeeded",
            summary=outcome.summary,
            platform_job_id=outcome.platform_job_id,
        )
        # Only this task's next run moved.
        await self._publish_task_states(task_id)

    async def _publish_task_states(self, task_id: str | None = None) -> None:
        """Record next run times: every task's, or only ``task_id``'s."""
        scheduler = self._scheduler
        if scheduler is None:
            return
        states = []
        for definition in self._catalog:
            if task_id is not None and definition.task_id != task_id:
                continue
            job = scheduler.get_job(definition.task_id)
            states.append((definition, job.next_run_time if job else None))
        await publish_task_states(states)

    async def _start_leader_services(self) -> None:
        """Install the full schedule catalog on this replica."""
        if self._scheduler is not None:
            return
        scheduler = AsyncIOScheduler(timezone=timezone.utc)
        now = datetime.now(timezone.utc)
        startup_slot = 0
        for definition in self._catalog:
            options: dict[str, Any] = dict(MISFIRE_OPTIONS)
            if definition.run_at_startup:
                options["next_run_time"] = now + timedelta(
                    seconds=startup_slot * STARTUP_STAGGER_SECONDS
                )
                startup_slot += 1
            scheduler.add_job(
                self._run_scheduled_task,
                build_trigger(definition),
                id=definition.task_id,
                name=definition.name,
                replace_existing=True,
                args=[definition.task_id, resolve_callback(definition.callback)],
                **options,
            )
        scheduler.start()
        self._scheduler = scheduler
        await self._publish_task_states()
        logger.info(
            "Trigger leader installed scheduled tasks",
            extra={"owner_id": self.owner_id, "tasks": len(self._catalog)},
        )

    async def _stop_leader_services(self) -> None:
        """Stop triggers and cancel running callbacks before surrendering."""
        if self._scheduler is None:
            return
        scheduler = self._scheduler
        self._scheduler = None
        scheduler.shutdown(wait=False)
        logger.info("Trigger leader stopped scheduled tasks")

    async def _renew_within_lease(self) -> bool:
        remaining = self.lease.seconds_remaining()
        if remaining <= 0:
            return False
        try:
            return await asyncio.wait_for(self.lease.renew(), timeout=remaining)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning("Trigger lease renewal did not finish before its deadline")
            return False
        except Exception:
            logger.exception("Failed to renew scheduler trigger lease")
            return False

    async def _surrender(self) -> None:
        try:
            await self._stop_leader_services()
        except Exception:
            logger.exception("Failed to stop trigger leader services")
        try:
            await self.lease.release()
        except Exception:
            logger.exception("Failed to release scheduler trigger lease")

    async def _leadership_loop(self) -> None:
        """Elect one trigger leader among all control replicas."""
        try:
            while not self._stop_event.is_set():
                if not self.lease.is_leader:
                    try:
                        acquired = await self.lease.try_acquire()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Failed to acquire scheduler trigger lease")
                        acquired = False
                    if not acquired:
                        await wait_or_stop(self._stop_event, self._retry_seconds)
                        continue

                    logger.info(
                        "Control replica became trigger leader",
                        extra={"owner_id": self.owner_id},
                    )
                    try:
                        await self._start_leader_services()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Failed to start trigger leader services")
                        await self._surrender()
                        await wait_or_stop(self._stop_event, self._retry_seconds)
                        continue

                await wait_or_stop(self._stop_event, self._renew_seconds)
                if self._stop_event.is_set():
                    break
                if await self._renew_within_lease():
                    continue

                logger.warning(
                    "Control replica lost trigger leadership",
                    extra={"owner_id": self.owner_id},
                )
                await self._surrender()
        finally:
            await self._surrender()


__all__ = [
    "STARTUP_STAGGER_SECONDS",
    "ControlPlane",
    "LEADERSHIP_RENEW_SECONDS",
    "LEADERSHIP_RETRY_SECONDS",
    "build_trigger",
    "replica_heartbeat_loop",
    "resolve_callback",
    "wait_or_stop",
]
