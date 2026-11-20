"""API-owned admission for one Kubernetes compute incarnation.

The build controller still owns physical Job creation/deletion. The normal
remote host owns execution; the pod receives only an attempt-scoped control key.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from sqlalchemy import select

from shared.worker_control_auth import kubernetes_compute_control_key
from src.core.database import get_db_context, get_liveness_db_context
from src.jobs.platform.catalog import get_platform_job_metadata
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob, LEASE_DURATION
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import publish_platform_job_update, set_platform_job_executor_attribution
from src.worker.remote.fencing import current_remote_lease_ref, fence_remote_owner
from src.worker.remote.protocol import AuthenticationError

if TYPE_CHECKING:
    from src.jobs.platform.kubernetes_client import KubernetesJobClient


def compute_worker_id(lease_token: UUID) -> UUID:
    return uuid5(lease_token, "bifrost.kubernetes.compute.v1")


async def sync_compute_worker(job_id: UUID) -> None:
    """Bind an existing reservation before the controller unsuspends its Job.

    Match the attempt again after locking worker → job, the same order used
    by canonical remote mutations. No controller may overwrite a newer retry.
    """
    async with get_db_context() as db:
        snapshot = await db.get(PlatformJob, job_id)
        if snapshot is None or snapshot.lease_token is None:
            return
        token = snapshot.lease_token
        worker = await db.scalar(select(ExecutionWorker).where(
            ExecutionWorker.id == compute_worker_id(token),
        ).with_for_update())
        if worker is None:
            return
        job = await db.scalar(select(PlatformJob).where(
            PlatformJob.id == job_id, PlatformJob.lease_token == token,
            PlatformJob.execution_backend == "kubernetes",
        ).with_for_update().execution_options(populate_existing=True))
        if job is None:
            return
        if (worker.form != "job" or worker.resource_class != "isolated"
                or worker.isolated_platform_job_id != job.id or worker.isolated_platform_job_attempt != job.attempt):
            raise RuntimeError("Kubernetes compute reservation changed")
        if worker.kubernetes_job_uid not in (None, job.kubernetes_job_uid):
            raise RuntimeError("Kubernetes compute Job UID changed")
        if job.kubernetes_job_uid and worker.state == "reserved":
            worker.kubernetes_job_uid = job.kubernetes_job_uid
            worker.state = "starting"


async def compute_control_key(worker_id: UUID, root_key: str) -> str | None:
    """Resolve only reserved compute incarnations; terminal attempts revoke auth."""
    async with get_liveness_db_context() as db:
        row = (await db.execute(select(ExecutionWorker, PlatformJob).outerjoin(
            PlatformJob, PlatformJob.id == ExecutionWorker.isolated_platform_job_id,
        ).where(ExecutionWorker.id == worker_id, ExecutionWorker.form == "job",
                ExecutionWorker.isolated_platform_job_id.is_not(None)))).one_or_none()
    if row is None:
        return None  # The existing reusable-worker authentication path.
    worker, job = row
    if (job is None or worker.state not in ("starting", "ready", "draining")
            or worker.resource_class != "isolated" or job.lease_token is None
            or compute_worker_id(job.lease_token) != worker_id
            or worker.isolated_platform_job_attempt != job.attempt
            or job.execution_backend != "kubernetes"
            or job.status not in ("running", "cancel_requested")
            or worker.kubernetes_job_uid != job.kubernetes_job_uid):
        raise AuthenticationError("Kubernetes compute attempt is no longer admissible")
    return kubernetes_compute_control_key(root_key, worker_id, job.lease_token)


async def adopt_compute_claim(
    worker_id: UUID, job_id: UUID, attempt: int, *, fits_bytes: int,
    memory_sample: tuple[int, int], job_types: frozenset[str],
) -> ClaimedPlatformJob | None:
    """Adopt the controller's preclaim once, under the remote owner fence."""
    ref = current_remote_lease_ref()
    if ref is None or ref.lease.worker_id != worker_id:
        return None
    async with get_db_context() as db:
        if not await fence_remote_owner(db):
            return None
        job = await db.scalar(select(PlatformJob).where(
            PlatformJob.id == job_id, PlatformJob.attempt == attempt,
            PlatformJob.execution_backend == "kubernetes", PlatformJob.status == "running",
            PlatformJob.error_code.is_(None), PlatformJob.runner_started_at.is_(None),
            PlatformJob.job_type.in_(job_types),
        ).with_for_update())
        worker = await db.get(ExecutionWorker, worker_id)
        if job is None or worker is None or job.lease_token is None:
            return None
        metadata = get_platform_job_metadata(job.job_type)
        current, limit = memory_sample
        if (compute_worker_id(job.lease_token) != worker_id
                or worker.form != "job" or worker.state != "ready"
                or worker.resource_class != "isolated" or worker.execution_lane != "normal"
                or worker.supported_handlers or metadata is None
                or not metadata.required_compute_capabilities
                or not metadata.required_compute_capabilities.issubset(worker.supported_compute)
                or worker.isolated_platform_job_id != job.id
                or worker.isolated_platform_job_attempt != attempt
                or not worker.kubernetes_pod_uid
                or worker.kubernetes_job_uid != job.kubernetes_job_uid
                or job.memory_required_bytes > fits_bytes or current < 0 or limit <= 0):
            return None
        now = datetime.now(timezone.utc)
        job.kubernetes_pod_uid = worker.kubernetes_pod_uid
        job.lease_owner = f"kubernetes-compute:{worker_id}"
        job.runner_started_at = job.heartbeat_at = now
        job.lease_expires_at = now + LEASE_DURATION
        job.memory_start_bytes = job.memory_peak_bytes = current
        job.memory_limit_bytes = limit
        job.phase = "Starting"
        job.revision += 1
        set_platform_job_executor_attribution(job, worker, compute_capability=metadata.compute_capability)
        claim = ClaimedPlatformJob(job.id, job.lease_token, job.timeout_seconds,
                                   metadata.policy.hard_memory_ratio, job.memory_required_bytes, job.job_type)
    await publish_platform_job_update(job)
    return claim


async def cleanup_compute_workers(job_id: UUID, client: KubernetesJobClient) -> None:
    """Retire obsolete incarnations only after exact-UID physical cleanup.

    This is part of the existing build-controller pass, not a new janitor.
    It also covers cancelled-before-hello and controller-create reply loss.
    """
    async with get_db_context() as db:
        workers = list(await db.scalars(select(ExecutionWorker).where(
            ExecutionWorker.form == "job", ExecutionWorker.isolated_platform_job_id == job_id,
            ExecutionWorker.end_reason.is_distinct_from("isolated_resource_deleted"),
        ).with_for_update()))
        job = await db.get(PlatformJob, job_id)
        for worker in workers:
            if (job is not None and job.status in ("running", "cancel_requested")
                    and job.attempt == worker.isolated_platform_job_attempt
                    and job.kubernetes_job_name == worker.kubernetes_job_name):
                continue
            if worker.kubernetes_namespace != client.namespace:
                raise RuntimeError("Kubernetes compute cleanup namespace changed")
            remote = await client.get_job(worker.kubernetes_job_name)
            uid = worker.kubernetes_job_uid
            if remote is not None:
                if uid is None:
                    if remote.get("metadata", {}).get("labels", {}).get(
                        "bifrost.gobifrost.com/worker-incarnation"
                    ) != str(worker.id):
                        raise RuntimeError("Unbound Kubernetes compute Job identity changed")
                    worker.kubernetes_job_uid = remote["metadata"]["uid"]
                    continue  # Commit the immutable binding before deleting.
                if remote["metadata"]["uid"] != uid:
                    raise RuntimeError("Kubernetes compute cleanup Job UID changed")
                await client.delete_job(worker.kubernetes_job_name, uid)
                continue
            if uid is not None:
                pods = await client.list_pods(uid)
                if any(p.get("status", {}).get("phase") not in ("Succeeded", "Failed") for p in pods):
                    continue
            if worker.state != "retired":
                worker.state = "lost"
            worker.end_reason = "isolated_resource_deleted"
            worker.ended_at = worker.ended_at or datetime.now(timezone.utc)
