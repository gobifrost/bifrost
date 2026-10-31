"""Read-only canonical demand for physical worker capacity."""

from collections import Counter
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform.catalog import get_platform_job_metadata
from src.models.orm.platform_jobs import PlatformJob
from src.services.kubernetes_execution import KubernetesPolicy, effective_job_max_concurrency


async def pending_local_platform_job_ids(
    db: AsyncSession, policy: KubernetesPolicy, now: datetime,
) -> list[UUID]:
    """Count ready local jobs without needing an executor to mark memory wait.

    This predicts admissible parallel work; it never acquires a job lease.
    Global type limits and resource exclusions include isolated jobs and jobs
    cancelling in place. Pending jobs consume virtual places in claim priority
    order so a serial backlog cannot cause parallel workers to be provisioned.
    The actual claimant still enforces its transactional locks and memory gate.
    Only metadata is read, never job payloads or encrypted inputs.
    """
    active = (await db.execute(
        select(PlatformJob.job_type, PlatformJob.resource_lock_key)
        .where(PlatformJob.status.in_(("running", "cancel_requested")))
    )).all()
    counts = Counter(job_type for job_type, _key in active)
    resources = {key for _job_type, key in active if key is not None}
    queued = (await db.execute(
        select(PlatformJob.id, PlatformJob.job_type, PlatformJob.resource_lock_key)
        .where(
            PlatformJob.status == "queued",
            PlatformJob.execution_backend == "local",
            PlatformJob.available_at <= now,
        )
        .order_by(PlatformJob.priority.desc(), PlatformJob.created_at, PlatformJob.id)
    )).all()
    eligible: list[UUID] = []
    for job_id, job_type, resource_key in queued:
        metadata = get_platform_job_metadata(job_type)
        if metadata is None:
            continue  # This build cannot provide capacity for an unknown handler.
        maximum = effective_job_max_concurrency(metadata, policy)
        if maximum is not None and counts[job_type] >= maximum:
            continue
        if resource_key is not None and resource_key in resources:
            continue
        eligible.append(job_id)
        counts[job_type] += 1
        if resource_key is not None:
            resources.add(resource_key)
    return eligible
