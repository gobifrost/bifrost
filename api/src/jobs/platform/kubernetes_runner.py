"""Kubernetes pod entrypoint for one remotely claimed platform job."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from src.core.database import close_db, get_db_context, init_db
from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.schedulers.platform_jobs import (
    LEASE_DURATION,
    ClaimedPlatformJob,
    run_claimed_platform_job,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.execution.memory_monitor import get_cgroup_memory
from src.services.platform_jobs import publish_platform_job_update


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def admit_kubernetes_platform_job(
    job_id: UUID,
    lease_token: UUID,
    *,
    kubernetes_job_uid: str,
    kubernetes_pod_uid: str,
) -> ClaimedPlatformJob | None:
    """Bind the first Kubernetes pod for a preclaimed remote platform job."""
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(PlatformJob)
                .where(
                    PlatformJob.id == job_id,
                    PlatformJob.lease_token == lease_token,
                    PlatformJob.execution_backend == "kubernetes",
                    PlatformJob.status == "running",
                    PlatformJob.error_code.is_(None),
                    PlatformJob.kubernetes_job_uid == kubernetes_job_uid,
                    PlatformJob.kubernetes_pod_uid.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return None

        definition = get_platform_job_definition(job.job_type)
        if definition is None or definition.policy.execution_class != "build":
            return None

        now = _now()
        current_memory, memory_limit = get_cgroup_memory()
        job.kubernetes_pod_uid = kubernetes_pod_uid
        job.lease_owner = f"kubernetes-pod:{kubernetes_pod_uid}"
        job.runner_started_at = now
        job.heartbeat_at = now
        job.lease_expires_at = now + LEASE_DURATION
        if current_memory >= 0:
            job.memory_start_bytes = current_memory
            job.memory_peak_bytes = current_memory
        if memory_limit > 0:
            job.memory_limit_bytes = memory_limit
        job.phase = "Starting"
        job.revision += 1
        claim = ClaimedPlatformJob(
            id=job.id,
            lease_token=lease_token,
            timeout_seconds=job.timeout_seconds,
            hard_memory_ratio=definition.policy.hard_memory_ratio,
            job_type=job.job_type,
        )
        await db.commit()
    await publish_platform_job_update(job)
    return claim


async def run_kubernetes_platform_job(job_id: UUID, lease_token: UUID) -> bool:
    kubernetes_job_uid = os.environ.get("BIFROST_KUBERNETES_JOB_UID")
    kubernetes_pod_uid = os.environ.get("BIFROST_KUBERNETES_POD_UID")
    if not kubernetes_job_uid or not kubernetes_pod_uid:
        return False

    claim = await admit_kubernetes_platform_job(
        job_id,
        lease_token,
        kubernetes_job_uid=kubernetes_job_uid,
        kubernetes_pod_uid=kubernetes_pod_uid,
    )
    if claim is None:
        return False

    task = asyncio.create_task(run_claimed_platform_job(claim))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        return await task
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


async def _main(job_id: str, lease_token: str) -> int:
    await init_db()
    try:
        completed = await run_kubernetes_platform_job(UUID(job_id), UUID(lease_token))
        return 0 if completed else 1
    finally:
        await close_db()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: platform.kubernetes_runner JOB_ID LEASE_TOKEN")
    raise SystemExit(asyncio.run(_main(sys.argv[1], sys.argv[2])))


__all__ = [
    "admit_kubernetes_platform_job",
    "run_kubernetes_platform_job",
]
