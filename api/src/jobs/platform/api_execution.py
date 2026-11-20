"""In-process execution backend for API-owned PlatformJobs.

The scheduler retains the durable lease, heartbeat, cancellation, retry, and
status protocol.  This backend only replaces the local child process for
handlers that are explicitly classified as API control-plane or I/O work.
"""

from __future__ import annotations

import asyncio
import logging

from src.jobs.platform.runner import run_claimed_platform_job
from src.jobs.platform.catalog import get_platform_job_metadata
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob
from src.services.execution.memory_monitor import effective_memory_sample


logger = logging.getLogger(__name__)


class _ApiPlatformJobProcess:
    """A ``PlatformJobProcess`` backed by one in-process asyncio task."""

    def __init__(self, claim: ClaimedPlatformJob) -> None:
        self._claim = claim
        self._task = asyncio.create_task(
            self._run(), name=f"api-platform-job-{claim.id}"
        )

    async def _run(self) -> int:
        try:
            completed = await run_claimed_platform_job(
                self._claim.id, self._claim.lease_token
            )
            return 0 if completed else 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "API platform-job handler exited unexpectedly",
                extra={"job_id": str(self._claim.id)},
            )
            return 1

    @property
    def returncode(self) -> int | None:
        if not self._task.done():
            return None
        if self._task.cancelled():
            return 1
        return self._task.result()

    async def wait(self) -> int:
        try:
            return await asyncio.shield(self._task)
        except asyncio.CancelledError:
            if self._task.cancelled():
                return 1
            raise

    async def terminate(self) -> None:
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


class ApiPlatformJobExecutionBackend:
    """Run explicitly API-owned jobs without assigning a physical worker."""

    async def spawn(self, claim: ClaimedPlatformJob) -> _ApiPlatformJobProcess:
        metadata = get_platform_job_metadata(claim.job_type)
        if metadata is None or metadata.execution_owner != "api":
            raise ValueError(f"Platform job {claim.job_type!r} is not API-owned")
        return _ApiPlatformJobProcess(claim)

    def memory(self) -> tuple[int, int]:
        return effective_memory_sample()


__all__ = ["ApiPlatformJobExecutionBackend"]
