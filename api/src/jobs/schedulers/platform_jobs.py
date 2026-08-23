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
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text

from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobPolicy
from src.jobs.platform.registry import get_platform_job_definition
from src.models.orm.platform_jobs import PlatformJob
from src.services.execution.memory_monitor import get_cgroup_memory
from src.services.platform_job_memory_profiles import (
    record_platform_job_memory_profile,
)
from src.services.platform_jobs import (
    publish_platform_job_update,
)

LEASE_DURATION = timedelta(seconds=30)
HEARTBEAT_INTERVAL_SECONDS = 5
RUNNER_TERMINATE_GRACE_SECONDS = 5
RUNNER_RETRY_DELAY = timedelta(seconds=5)
PLATFORM_JOB_IDLE_SECONDS = 2.0

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


def _memory_allows_start(
    policy: PlatformJobPolicy,
    *,
    memory_required_bytes: int,
) -> bool:
    current, limit = get_cgroup_memory()
    if current < 0 or limit <= 0:
        return True
    headroom_bytes = max(limit - current, 0)
    return (
        current / limit <= policy.admission_memory_ratio
        and headroom_bytes >= memory_required_bytes
    )


def _memory_exceeds_hard_limit(hard_ratio: float) -> bool:
    current, limit = get_cgroup_memory()
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


async def claim_platform_job() -> ClaimedPlatformJob | None:
    """Claim one admissible queued job using row locking and a fenced lease."""
    updated: list[PlatformJob] = []
    async with get_db_context() as db:
        candidate_ids = (
            (
                await db.execute(
                    select(PlatformJob.id)
                    .where(
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
                        PlatformJob.status == "queued",
                        PlatformJob.available_at <= _now(),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if job is None:
                continue
            definition = get_platform_job_definition(job.job_type)
            if definition is None:
                job.status = "failed"
                job.phase = "Failed"
                job.error_code = "unknown_job_type"
                job.error_message = f"No handler is registered for {job.job_type}."
                job.error_retryable = False
                job.completed_at = _now()
                job.revision += 1
                updated.append(job)
                continue
            if definition.policy.max_concurrency is not None:
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
                if running_count >= definition.policy.max_concurrency:
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
            if not _memory_allows_start(
                definition.policy,
                memory_required_bytes=job.memory_required_bytes,
            ):
                if job.phase != "Waiting for scheduler memory":
                    job.phase = "Waiting for scheduler memory"
                    job.revision += 1
                    updated.append(job)
                continue

            now = _now()
            token = uuid4()
            job.status = "running"
            job.phase = "Starting"
            job.attempt += 1
            job.started_at = job.started_at or now
            job.lease_owner = f"{socket.gethostname()}:{os.getpid()}"
            job.lease_token = token
            job.heartbeat_at = now
            job.lease_expires_at = now + LEASE_DURATION
            job.error_code = None
            job.error_message = None
            job.error_retryable = None
            current_memory, memory_limit = get_cgroup_memory()
            job.memory_start_bytes = current_memory if current_memory >= 0 else None
            job.memory_peak_bytes = current_memory if current_memory >= 0 else None
            job.memory_limit_bytes = memory_limit if memory_limit > 0 else None
            job.revision += 1
            await db.commit()
            claimed = ClaimedPlatformJob(
                id=job.id,
                lease_token=token,
                timeout_seconds=job.timeout_seconds,
                hard_memory_ratio=definition.policy.hard_memory_ratio,
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
) -> str | None:
    async with get_db_context() as db:
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
        current_memory, memory_limit = get_cgroup_memory()
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


async def _handle_runner_loss(
    job_id: UUID,
    lease_token: UUID,
    *,
    error_code: str,
    error_message: str,
) -> bool:
    """Retry or fail an attempt, fenced against a newer scheduler owner."""
    async with get_db_context() as db:
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
        await record_platform_job_memory_profile(db, job)
        _clear_lease(job)
        job.revision += 1
        await db.commit()
    await publish_platform_job_update(job)
    return job.status == "queued"


async def run_claimed_platform_job(claim: ClaimedPlatformJob) -> bool:
    """Run one handler in a killable child process while maintaining its lease."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.jobs.platform.runner",
        str(claim.id),
        str(claim.lease_token),
        start_new_session=True,
    )
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

            status = await _heartbeat(claim.id, claim.lease_token)
            if status is None:
                await _terminate_runner(process)
                return True
            if status == "cancel_requested":
                await _terminate_runner(process)
                await _handle_runner_loss(
                    claim.id,
                    claim.lease_token,
                    error_code="cancelled",
                    error_message="Platform job was cancelled.",
                )
                return True
            if _monotonic() - started >= claim.timeout_seconds:
                await _terminate_runner(process)
                await _handle_runner_loss(
                    claim.id,
                    claim.lease_token,
                    error_code="timeout",
                    error_message=(
                        f"Platform job timed out after {claim.timeout_seconds} seconds."
                    ),
                )
                return False
            if _memory_exceeds_hard_limit(claim.hard_memory_ratio):
                await _terminate_runner(process)
                await _handle_runner_loss(
                    claim.id,
                    claim.lease_token,
                    error_code="memory_pressure",
                    error_message=(
                        "Platform job was stopped before the scheduler container "
                        "exceeded its memory limit."
                    ),
                )
                return False
    except asyncio.CancelledError:
        await _terminate_runner(process)
        try:
            await _handle_runner_loss(
                claim.id,
                claim.lease_token,
                error_code="runner_shutdown",
                error_message="Platform-job runner stopped during scheduler shutdown.",
            )
        except Exception:
            logger.exception(
                "Failed to release platform job after worker shutdown",
                extra={"job_id": str(claim.id)},
            )
        raise

    async with get_db_context() as db:
        job = await db.get(PlatformJob, claim.id)
        if job is not None and job.status in ("succeeded", "failed", "cancelled"):
            return job.status == "succeeded"

    await _handle_runner_loss(
        claim.id,
        claim.lease_token,
        error_code="runner_exited",
        error_message=(
            f"Platform-job runner exited with code {process.returncode} "
            "before recording a result."
        ),
    )
    return False


async def process_platform_jobs() -> tuple[int, int]:
    """Recover expired leases, then run at most one queued platform job."""
    recovered, recovery_failures = await recover_expired_platform_jobs()
    claim = await claim_platform_job()
    if claim is None:
        return recovered, recovery_failures
    succeeded = await run_claimed_platform_job(claim)
    return recovered + 1, recovery_failures + (0 if succeeded else 1)


async def platform_job_worker_loop(
    shutdown_event: asyncio.Event,
    *,
    idle_seconds: float = PLATFORM_JOB_IDLE_SECONDS,
) -> None:
    """Continuously claim one job at a time for this scheduler replica."""
    while not shutdown_event.is_set():
        processed = 0
        try:
            processed, _ = await process_platform_jobs()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Platform-job worker iteration failed")

        if processed:
            continue

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=idle_seconds)
        except TimeoutError:
            # The idle interval elapsed; the next loop iteration may claim work.
            continue


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "LEASE_DURATION",
    "claim_platform_job",
    "platform_job_worker_loop",
    "process_platform_jobs",
    "recover_expired_platform_jobs",
    "run_claimed_platform_job",
]
