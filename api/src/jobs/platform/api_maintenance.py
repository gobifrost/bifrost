"""API-hosted execution for lightweight control-plane PlatformJobs.

The durable PlatformJob row, claim lease, canonical runner, status transport,
and retry policy remain shared. This module only supplies bounded admission for
maintenance that must survive a scale-to-zero physical worker pool.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from src.config import Settings, get_settings
from src.jobs.platform.api_execution import ApiPlatformJobExecutionBackend
from src.jobs.platform.catalog import (
    api_control_platform_job_types,
    api_platform_job_types,
)
from src.jobs.schedulers.platform_jobs import (
    ClaimedPlatformJob,
    claim_platform_job,
    run_claimed_platform_job,
)
from src.services.execution.memory_monitor import effective_memory_sample
from src.services.memory_requests import MemoryBudget
from src.services.work_dispatch import WAKE_PLATFORM_JOB, listen_for_pool_wake


logger = logging.getLogger(__name__)
SWEEP_SECONDS = 15.0
MAX_API_MAINTENANCE_TASKS = 4
MAX_API_NON_CONTROL_TASKS = MAX_API_MAINTENANCE_TASKS - 1


@dataclass(frozen=True)
class _ActiveApiJob:
    claim_memory_bytes: int
    control: bool
    task: asyncio.Task[None]


class ApiMaintenanceExecutor:
    """Bounded API execution for catalogued control-plane and I/O jobs."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._admission_lock = asyncio.Lock()
        self._active: dict[UUID, _ActiveApiJob] = {}
        self._backend = ApiPlatformJobExecutionBackend()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="api-maintenance-executor"
            )

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        active = [item.task for item in self._active.values()]
        for active_task in active:
            active_task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    async def _claim_and_run_once(self) -> bool:
        """Reserve one bounded in-process slot and claim a fitting job.

        The lock deliberately spans only memory admission, the durable claim,
        and local reservation.  Handler I/O runs after release so unrelated
        jobs retain parallelism and no cloud call holds the admission lock.
        """
        async with self._admission_lock:
            if self._stop.is_set() or len(self._active) >= MAX_API_MAINTENANCE_TASKS:
                return False

            current, limit = effective_memory_sample()
            budget = MemoryBudget(
                limit_bytes=limit,
                baseline_bytes=current,
                threshold=self._settings.memory_pressure_threshold,
            ).budget_bytes
            reserved_memory = sum(item.claim_memory_bytes for item in self._active.values())
            available_memory = budget - reserved_memory
            if available_memory <= 0:
                return False

            non_control_count = sum(
                not item.control for item in self._active.values()
            )
            job_types = (
                api_control_platform_job_types()
                if non_control_count >= MAX_API_NON_CONTROL_TASKS
                else api_platform_job_types()
            )
            claim = await claim_platform_job(
                backend="local",
                fits_bytes=available_memory,
                job_types=job_types,
            )
            if claim is None:
                return False

            control = claim.job_type in api_control_platform_job_types()
            task = asyncio.create_task(
                self._run_claim(claim), name=f"api-maintenance-job-{claim.id}"
            )
            self._active[claim.id] = _ActiveApiJob(
                claim_memory_bytes=claim.memory_required_bytes,
                control=control,
                task=task,
            )
            return True

    async def _run_claim(self, claim: ClaimedPlatformJob) -> None:
        try:
            await run_claimed_platform_job(claim, self._backend)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "API maintenance platform-job runner failed",
                extra={"job_id": str(claim.id)},
            )
        finally:
            async with self._admission_lock:
                self._active.pop(claim.id, None)
            self._wake.set()

    async def _run(self) -> None:
        listener = asyncio.create_task(
            listen_for_pool_wake(
                self._settings.redis_url,
                {WAKE_PLATFORM_JOB: self._wake},
            ),
            name="api-maintenance-wake",
        )
        try:
            while not self._stop.is_set():
                self._wake.clear()
                admitted = False
                while not self._stop.is_set():
                    try:
                        ran = await self._claim_and_run_once()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("API maintenance platform-job claim failed")
                        ran = False
                    if not ran:
                        break
                    admitted = True
                if admitted:
                    continue
                stop = asyncio.create_task(self._stop.wait())
                wake = asyncio.create_task(self._wake.wait())
                try:
                    await asyncio.wait(
                        {stop, wake},
                        timeout=SWEEP_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    stop.cancel()
                    wake.cancel()
                    await asyncio.gather(stop, wake, return_exceptions=True)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)


__all__ = ["ApiMaintenanceExecutor"]
