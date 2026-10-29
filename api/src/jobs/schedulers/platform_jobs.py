"""Scheduler host for durable, isolated platform jobs."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text

from shared.app_build_errors import APP_BUILD_JOB_TYPES, app_build_memory_message
from src.core.database import get_db_context
from src.jobs.platform.catalog import get_platform_job_metadata
from src.models.orm.platform_jobs import PlatformJob
from src.services.execution.memory_monitor import get_cgroup_memory
from src.services.platform_job_memory_profiles import (
    record_platform_job_memory_profile,
)
from src.services.platform_jobs import (
    EXPORT_RUNNER_LOST,
    WAITING_FOR_MEMORY,
    fail_linked_solution_export,
    publish_platform_job_update,
)
from src.services.worker_pool.sizing import JobSizing

LEASE_DURATION = timedelta(seconds=30)
HEARTBEAT_INTERVAL_SECONDS = 5
RUNNER_TERMINATE_GRACE_SECONDS = 5
RUNNER_RETRY_DELAY = timedelta(seconds=5)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _monotonic() -> float:
    return time.monotonic()


def _clear_lease(job: PlatformJob) -> None:
    job.lease_owner = None
    job.lease_token = None
    job.heartbeat_at = None
    job.lease_expires_at = None


def _memory_exceeds_hard_limit(
    hard_ratio: float,
    memory_sample: tuple[int, int] | None = None,
) -> bool:
    current, limit = memory_sample or get_cgroup_memory()
    return current >= 0 and limit > 0 and current / limit >= hard_ratio


async def recover_expired_platform_jobs() -> tuple[int, int]:
    """Recover leases after scheduler/container loss."""
    now = _now()
    recovered: list[PlatformJob] = []
    async with get_db_context() as db:
        jobs = (
            (
                await db.execute(
                    select(PlatformJob)
                    .where(
                        PlatformJob.execution_backend == "local",
                        PlatformJob.status.in_(("running", "cancel_requested")),
                        or_(
                            PlatformJob.lease_expires_at.is_(None),
                            PlatformJob.lease_expires_at <= now,
                        ),
                    )
                    .order_by(PlatformJob.lease_expires_at.asc())
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        failed = 0
        for job in jobs:
            if job.status == "cancel_requested":
                job.status = "cancelled"
                job.phase = "Cancelled after runner stopped"
                job.completed_at = now
            elif job.retry_on_runner_loss and job.attempt < job.max_attempts:
                job.status = "queued"
                job.phase = "Recovered after runner stopped"
                job.available_at = now + RUNNER_RETRY_DELAY
            else:
                job.status = "failed"
                job.phase = "Failed"
                job.error_code = "runner_lost"
                job.error_message = (
                    "The platform-job runner stopped before the operation completed."
                )
                job.error_retryable = False
                job.completed_at = now
                await fail_linked_solution_export(
                    db, job, completed_at=now, failure_message=EXPORT_RUNNER_LOST
                )
                failed += 1
            await record_platform_job_memory_profile(db, job)
            _clear_lease(job)
            job.revision += 1
            recovered.append(job)
        await db.commit()

    for job in recovered:
        await publish_platform_job_update(job)
    return len(recovered), failed


@dataclass(frozen=True)
class ClaimedPlatformJob:
    id: UUID
    lease_token: UUID
    timeout_seconds: int
    hard_memory_ratio: float
    memory_required_bytes: int
    job_type: str = ""


class PlatformJobProcess(Protocol):
    """A runner process controlled by a platform-job execution backend."""

    @property
    def returncode(self) -> int | None:
        """Return the exit code once the runner has exited."""
        raise NotImplementedError

    async def wait(self) -> int:
        """Wait for the physical runner to exit."""
        raise NotImplementedError

    async def terminate(self) -> None:
        """Terminate this attempt's physical runner."""
        raise NotImplementedError


class PlatformJobExecutionBackend(Protocol):
    """Starts and observes one claimed platform-job runner."""

    async def spawn(self, claim: ClaimedPlatformJob) -> PlatformJobProcess:
        """Start the handler for an already-claimed attempt."""
        raise NotImplementedError

    def memory(self) -> tuple[int, int]:
        """Return current container usage and effective memory limit."""
        raise NotImplementedError


def _remote_job_name(job_id: UUID, lease_token: UUID) -> str:
    # Must stay identical to kubernetes_client._job_name: the controller
    # re-renders the manifest from the persisted attempt to verify ownership.
    # The fingerprint (not the bearer token) goes into the name.
    from src.jobs.platform.kubernetes_client import _lease_token_fingerprint

    return f"bifrost-job-{job_id.hex}-{_lease_token_fingerprint(lease_token)[:12]}"


@dataclass(frozen=True)
class IsolatedPods:
    """Where isolated pods run, how many may (Max isolated pods), and their
    sizes (the worker sizing rule)."""

    namespace: str
    max_pods: int
    sizing: JobSizing
    # The deployment's worker memory request, which a standard pod requests.
    standard_request_bytes: int

    def pod_memory(self, required_bytes: int) -> tuple[int, int] | None:
        """The memory request and limit of the pod that holds
        ``required_bytes``: a standard pod requests the deployment's
        request, a sized one its whole limit; None when no pod can."""
        limit = self.sizing.pod_limit_bytes(required_bytes)
        if limit is None:
            return None
        if limit == self.sizing.standard_limit_bytes:
            return self.standard_request_bytes, limit
        return limit, limit


async def claim_platform_job(
    *,
    backend: str = "local",
    fits_bytes: int | None = None,
    isolated: IsolatedPods | None = None,
    memory_sample: tuple[int, int] | None = None,
) -> ClaimedPlatformJob | None:
    """Claim one admissible queued job using row locking and a fenced lease.

    A local job is claimed only when its memory requirement fits
    ``fits_bytes``, the claiming executor's free budget
    (src.services.memory_requests); it waits otherwise.

    An isolated job (``backend="kubernetes"``) is claimed for a pod in the
    ``isolated`` namespace while fewer than its maximum run, and records the
    pod's memory request and limit. One no pod can hold is left queued for
    the unfit sweep to fail visibly.
    """
    if backend == "local" and fits_bytes is None:
        raise ValueError("A local platform-job claim needs the executor's free budget")
    if (backend == "kubernetes") != (isolated is not None):
        raise ValueError("An isolated platform-job claim, and only one, needs its pods")
    updated: list[PlatformJob] = []
    async with get_db_context() as db:
        from src.worker.remote.fencing import fence_remote_owner

        if not await fence_remote_owner(db):
            return None
        candidate_ids = (
            (
                await db.execute(
                    select(PlatformJob.id)
                    .where(
                        PlatformJob.execution_backend == backend,
                        PlatformJob.status == "queued",
                        PlatformJob.available_at <= _now(),
                    )
                    .order_by(PlatformJob.priority.desc(), PlatformJob.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        for candidate_id in candidate_ids:
            job = (
                await db.execute(
                    select(PlatformJob)
                    .where(
                        PlatformJob.id == candidate_id,
                        PlatformJob.execution_backend == backend,
                        PlatformJob.status == "queued",
                        PlatformJob.available_at <= _now(),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if job is None:
                continue
            if backend == "kubernetes" and job.kubernetes_job_name is not None:
                continue
            metadata = get_platform_job_metadata(job.job_type)
            if metadata is None:
                job.status = "failed"
                job.phase = "Failed"
                job.error_code = "unknown_job_type"
                job.error_message = f"No handler is registered for {job.job_type}."
                job.error_retryable = False
                job.completed_at = _now()
                job.revision += 1
                updated.append(job)
                continue
            # An isolated pod's memory, by the worker sizing rule.
            pod_memory: tuple[int, int] | None = None
            if isolated is not None:
                pod_memory = isolated.pod_memory(job.memory_required_bytes)
                if pod_memory is None:
                    continue  # No pod can hold it: the unfit sweep fails it.
            # Product concurrency overrides (Settings UI) replace the code
            # policy default when set. Read per claim so changes apply
            # without restarts; the row is a single indexed lookup.
            from src.services.kubernetes_execution import (
                KubernetesExecutionService,
            )

            max_concurrency = await KubernetesExecutionService(
                db
            ).effective_max_concurrency(metadata)
            if max_concurrency is not None:
                handler_lock = (
                    await db.execute(
                        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"bifrost:platform-job-type:{job.job_type}"},
                    )
                ).scalar_one()
                if not handler_lock:
                    continue
                running_count = (
                    await db.execute(
                        select(func.count(PlatformJob.id)).where(
                            PlatformJob.job_type == job.job_type,
                            PlatformJob.status.in_(("running", "cancel_requested")),
                        )
                    )
                ).scalar_one()
                if running_count >= max_concurrency:
                    continue
            if job.resource_lock_key is not None:
                resource_lock = (
                    await db.execute(
                        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                        {
                            "key": f"bifrost:platform-job-resource:{job.resource_lock_key}"
                        },
                    )
                ).scalar_one()
                if not resource_lock:
                    continue
                resource_busy = (
                    await db.execute(
                        select(func.count(PlatformJob.id)).where(
                            PlatformJob.resource_lock_key == job.resource_lock_key,
                            PlatformJob.status.in_(("running", "cancel_requested")),
                        )
                    )
                ).scalar_one()
                if resource_busy:
                    continue
            if isolated is not None:
                backend_lock = (
                    await db.execute(
                        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                        {"key": "bifrost:platform-job-backend:kubernetes"},
                    )
                ).scalar_one()
                if not backend_lock:
                    continue
                remote_running_count = (
                    await db.execute(
                        select(func.count(PlatformJob.id)).where(
                            PlatformJob.execution_backend == "kubernetes",
                            PlatformJob.status.in_(("running", "cancel_requested")),
                        )
                    )
                ).scalar_one()
                if remote_running_count >= isolated.max_pods:
                    continue
            elif fits_bytes is not None and job.memory_required_bytes > fits_bytes:
                if job.phase != WAITING_FOR_MEMORY:
                    job.phase = WAITING_FOR_MEMORY
                    job.revision += 1
                    updated.append(job)
                continue

            now = _now()
            token = uuid4()
            job.status = "running"
            job.phase = (
                "Waiting for Kubernetes capacity"
                if backend == "kubernetes"
                else "Starting"
            )
            job.attempt += 1
            job.started_at = job.started_at or now
            job.lease_owner = f"{socket.gethostname()}:{os.getpid()}"
            job.lease_token = token
            job.heartbeat_at = now
            job.lease_expires_at = now + LEASE_DURATION
            job.error_code = None
            job.error_message = None
            job.error_retryable = None
            if isolated is not None and pod_memory is not None:
                job.kubernetes_job_name = _remote_job_name(job.id, token)
                job.kubernetes_namespace = isolated.namespace
                job.kubernetes_job_uid = None
                job.kubernetes_pod_uid = None
                job.kubernetes_launch_started_at = now
                job.runner_started_at = None
                job.memory_start_bytes = None
                job.memory_peak_bytes = None
                job.memory_request_bytes, job.memory_limit_bytes = pod_memory
            else:
                current_memory, memory_limit = memory_sample or get_cgroup_memory()
                job.memory_start_bytes = current_memory if current_memory >= 0 else None
                job.memory_peak_bytes = current_memory if current_memory >= 0 else None
                job.memory_limit_bytes = memory_limit if memory_limit > 0 else None
            job.revision += 1
            await db.commit()
            claimed = ClaimedPlatformJob(
                id=job.id,
                lease_token=token,
                timeout_seconds=job.timeout_seconds,
                hard_memory_ratio=metadata.policy.hard_memory_ratio,
                memory_required_bytes=job.memory_required_bytes,
                job_type=job.job_type,
            )
            for changed_job in updated:
                await publish_platform_job_update(changed_job)
            await publish_platform_job_update(job)
            return claimed
        await db.commit()

    for changed_job in updated:
        await publish_platform_job_update(changed_job)
    return None


async def _heartbeat(
    job_id: UUID,
    lease_token: UUID,
    memory_sample: tuple[int, int] | None = None,
) -> str | None:
    async with get_db_context() as db:
        from src.worker.remote.fencing import fence_remote_owner

        if not await fence_remote_owner(db):
            return None
        job = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.id == job_id,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.status.in_(("running", "cancel_requested")),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return None
        now = _now()
        job.heartbeat_at = now
        job.lease_expires_at = now + LEASE_DURATION
        current_memory, memory_limit = memory_sample or get_cgroup_memory()
        if current_memory >= 0:
            job.memory_peak_bytes = max(job.memory_peak_bytes or 0, current_memory)
        if memory_limit > 0:
            job.memory_limit_bytes = memory_limit
        await db.commit()
        return job.status


async def _terminate_runner(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=RUNNER_TERMINATE_GRACE_SECONDS,
        )
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            # The runner exited after the grace timeout; reap it below.
            pass
        await process.wait()


class _LocalPlatformJobProcess:
    """Adapts a local subprocess to the execution-backend process contract."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def wait(self) -> int:
        return await self._process.wait()

    async def terminate(self) -> None:
        await _terminate_runner(self._process)


class _LocalPlatformJobExecutionBackend:
    """The scheduler's existing local subprocess runner behavior."""

    async def spawn(self, claim: ClaimedPlatformJob) -> PlatformJobProcess:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "src.jobs.platform.runner",
            str(claim.id),
            str(claim.lease_token),
            start_new_session=True,
        )
        return _LocalPlatformJobProcess(process)

    def memory(self) -> tuple[int, int]:
        return get_cgroup_memory()


async def _handle_runner_loss(
    job_id: UUID,
    lease_token: UUID,
    *,
    error_code: str,
    error_message: str,
    out_of_memory: bool = False,
    memory_sample: tuple[int, int] | None = None,
) -> bool:
    """Retry or fail an attempt, fenced against a newer scheduler owner.

    ``out_of_memory``: Kubernetes stopped the attempt's pod at its memory
    limit, so the job learns it needs more (a retry asks for it).
    """
    async with get_db_context() as db:
        from src.worker.remote.fencing import fence_remote_owner

        if not await fence_remote_owner(db):
            return False
        job = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.id == job_id,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.status.in_(("running", "cancel_requested")),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        if error_code == "memory_pressure" and job.job_type in APP_BUILD_JOB_TYPES:
            error_message = app_build_memory_message("oom" if out_of_memory else "pressure")
        now = _now()
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.phase = "Cancelled"
            job.completed_at = now
        elif job.retry_on_runner_loss and job.attempt < job.max_attempts:
            job.status = "queued"
            job.phase = "Retrying after runner stopped"
            job.available_at = now + RUNNER_RETRY_DELAY
            job.error_code = error_code
            job.error_message = error_message
            job.error_retryable = True
        else:
            job.status = "failed"
            job.phase = "Failed"
            job.error_code = error_code
            job.error_message = error_message
            job.error_retryable = False
            job.completed_at = now
            await fail_linked_solution_export(
                db, job, completed_at=now, failure_message=EXPORT_RUNNER_LOST
            )
        await record_platform_job_memory_profile(
            db,
            job,
            out_of_memory=out_of_memory,
            memory_sample=memory_sample,
        )
        _clear_lease(job)
        job.revision += 1
        await db.commit()
    await publish_platform_job_update(job)
    return job.status == "queued"


async def run_claimed_platform_job(
    claim: ClaimedPlatformJob,
    backend: PlatformJobExecutionBackend | None = None,
) -> bool:
    """Run one handler in a killable child process while maintaining its lease."""
    uses_local_default = backend is None
    execution_backend = (
        _LocalPlatformJobExecutionBackend() if backend is None else backend
    )
    process = await execution_backend.spawn(claim)
    last_memory_sample: tuple[int, int] | None = None
    try:
        started = _monotonic()
        while process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS,
                )
                break
            except TimeoutError:
                # Expected heartbeat tick while the runner is still active.
                pass

            memory_sample = None if uses_local_default else execution_backend.memory()
            last_memory_sample = memory_sample
            if memory_sample is None:
                status = await _heartbeat(claim.id, claim.lease_token)
            else:
                status = await _heartbeat(
                    claim.id,
                    claim.lease_token,
                    memory_sample,
                )
            if status is None:
                await process.terminate()
                return True
            if status == "cancel_requested":
                await process.terminate()
                await _handle_runner_loss(
                    claim.id,
                    claim.lease_token,
                    error_code="cancelled",
                    error_message="Platform job was cancelled.",
                    memory_sample=memory_sample,
                )
                return True
            if _monotonic() - started >= claim.timeout_seconds:
                await process.terminate()
                await _handle_runner_loss(
                    claim.id,
                    claim.lease_token,
                    error_code="timeout",
                    error_message=(
                        f"Platform job timed out after {claim.timeout_seconds} seconds."
                    ),
                    memory_sample=memory_sample,
                )
                return False
            exceeds_hard_limit = (
                _memory_exceeds_hard_limit(claim.hard_memory_ratio)
                if memory_sample is None
                else _memory_exceeds_hard_limit(
                    claim.hard_memory_ratio, memory_sample
                )
            )
            if exceeds_hard_limit:
                await process.terminate()
                await _handle_runner_loss(
                    claim.id,
                    claim.lease_token,
                    error_code="memory_pressure",
                    error_message=(
                        "Platform job was stopped before the runner container "
                        "exceeded its memory limit."
                    ),
                    memory_sample=memory_sample,
                )
                return False
    except asyncio.CancelledError:
        await process.terminate()
        try:
            await _handle_runner_loss(
                claim.id,
                claim.lease_token,
                error_code="runner_shutdown",
                error_message="Platform-job runner stopped during scheduler shutdown.",
                memory_sample=last_memory_sample,
            )
        except Exception:
            logger.exception(
                "Failed to release platform job after worker shutdown",
                extra={"job_id": str(claim.id)},
            )
        raise

    async with get_db_context() as db:
        job = await db.get(PlatformJob, claim.id)
        if job is not None and job.status in (
            "succeeded",
            "failed",
            "cancelled",
            "requires_action",
        ):
            return job.status == "succeeded"

    message = (
        app_build_memory_message("killed")
        if claim.job_type in APP_BUILD_JOB_TYPES and process.returncode in (-9, 137)
        else (
            f"Platform-job runner exited with code {process.returncode} "
            "before recording a result."
        )
    )
    await _handle_runner_loss(
        claim.id,
        claim.lease_token,
        error_code="runner_exited",
        error_message=message,
        memory_sample=last_memory_sample,
    )
    return False


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "LEASE_DURATION",
    "PlatformJobExecutionBackend",
    "PlatformJobProcess",
    "claim_platform_job",
    "recover_expired_platform_jobs",
    "run_claimed_platform_job",
]
