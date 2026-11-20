"""API-hosted execution for lightweight control-plane platform jobs.

The durable PlatformJob row, claim lease, runner subprocess, status transport,
and retry policy remain shared.  This module only supplies a bounded claimant
for maintenance that must survive a scale-to-zero physical worker pool.
"""

from __future__ import annotations

import asyncio
import logging

from src.config import Settings, get_settings
from src.jobs.platform.catalog import api_platform_job_types
from src.jobs.schedulers.platform_jobs import claim_platform_job, run_claimed_platform_job
from src.services.execution.memory_monitor import effective_memory_sample
from src.services.memory_requests import MemoryBudget
from src.services.work_dispatch import WAKE_PLATFORM_JOB, listen_for_pool_wake


logger = logging.getLogger(__name__)
SWEEP_SECONDS = 15.0


class ApiMaintenanceExecutor:
    """Claim one API-owned maintenance job at a time within API headroom."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="api-maintenance-executor"
            )

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _claim_and_run_once(self) -> bool:
        current, limit = effective_memory_sample()
        budget = MemoryBudget(
            limit_bytes=limit,
            baseline_bytes=current,
            threshold=self._settings.memory_pressure_threshold,
        ).budget_bytes
        if budget <= 0:
            return False
        claim = await claim_platform_job(
            backend="local",
            fits_bytes=budget,
            job_types=api_platform_job_types(),
        )
        if claim is None:
            return False
        await run_claimed_platform_job(claim)
        return True

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
                try:
                    ran = await self._claim_and_run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("API maintenance platform-job claim failed")
                    ran = False
                if ran:
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
