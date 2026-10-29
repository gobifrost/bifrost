"""Reconcile resource-isolated PlatformJob attempts without occupying local slots.

Each pass reads Settings -> Kubernetes. With isolated jobs on, a queued
isolated job is claimed for a pod sized by the worker sizing rule
(``worker_pool.sizing``) while fewer than Max isolated pods run. With them
off, nothing new is placed in Kubernetes: pods already running are
reconciled until they end, and queued isolated jobs not yet placed move to
the local backend, where a worker runs them, admitted by memory like any
local platform job.

Isolated pods run as worker Jobs do (``resolve_worker_job_spec``): the Job
identity settings when set, else a copy of this controller's pod. Each pod
is created with the memory request and limit its claim recorded.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any
from contextlib import suppress
from uuid import UUID

from sqlalchemy import select

from src.config import Settings, get_settings
from src.core.database import get_db_context
from src.jobs.platform.kubernetes_client import (
    KubernetesConfigurationError,
    KubernetesJobClient,
    build_job_manifest,
    oom_killed,
)
from src.jobs.platform.catalog import get_platform_job_metadata
from src.jobs.schedulers.platform_jobs import (
    ClaimedPlatformJob,
    IsolatedPods,
    _handle_runner_loss,
    claim_platform_job,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.kubernetes_execution import load_kubernetes_policy
from src.services.memory_requests import MIB
from src.services.platform_jobs import publish_platform_job_update
from src.services.worker_pool.manifest import WorkerJobSpec, resolve_job_spec_when_ready
from src.services.work_dispatch import listen_for_pool_wake, wait_for_next_pass
from src.services.worker_pool.sizing import load_job_sizing

logger = logging.getLogger(__name__)
_ACTIVE = ("running", "cancel_requested")
# While Kubernetes holds builds (or a pass just placed or returned one) the
# controller passes every BUILD_POLL_SECONDS. Otherwise it passes on a pool
# wake-up (every enqueue publishes one) and sweeps every BUILD_SWEEP_SECONDS.
BUILD_POLL_SECONDS = 2.0
BUILD_SWEEP_SECONDS = 15.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pods_stopped(pods: list[dict[str, Any]]) -> bool:
    return all(p.get("status", {}).get("phase") in ("Succeeded", "Failed") for p in pods)


# The runner's own memory guard reports the same code.
_OUT_OF_MEMORY = ("memory_pressure", "The build pod exceeded its memory limit.")


def _failure(job: dict[str, Any], pods: list[dict[str, Any]]) -> tuple[str, str]:
    for condition in job.get("status", {}).get("conditions", []):
        if condition.get("status") == "True" and condition.get("reason") == "DeadlineExceeded":
            return "timeout", "The Kubernetes build pod exceeded its deadline."
    return "runner_exited", "The build pod stopped before recording a result."


def _pending_phase(pods: list[dict[str, Any]]) -> str:
    for pod in pods:
        for condition in pod.get("status", {}).get("conditions", []):
            if condition.get("type") == "PodScheduled" and condition.get("status") == "False":
                return "Waiting for Kubernetes capacity: pod cannot be scheduled"
        for container in pod.get("status", {}).get("containerStatuses", []):
            reason = container.get("state", {}).get("waiting", {}).get("reason")
            if reason in ("ErrImagePull", "ImagePullBackOff", "CreateContainerConfigError"):
                return f"Waiting for Kubernetes startup: {reason}"
    return "Waiting for Kubernetes build pod"


async def _return_unplaced_to_local(db: Any) -> list[PlatformJob]:
    """Queued isolated jobs not yet placed in Kubernetes move to the local
    backend (isolated jobs are off)."""
    jobs = (
        await db.scalars(
            select(PlatformJob)
            .where(
                PlatformJob.status == "queued",
                PlatformJob.execution_backend == "kubernetes",
                PlatformJob.kubernetes_job_name.is_(None),
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for job in jobs:
        job.execution_backend = "local"
        if job.phase is not None and job.phase.startswith("Waiting for Kubernetes"):
            job.phase = "Queued"
        job.revision += 1
    return list(jobs)


class KubernetesBuildController:
    def __init__(
        self, client: KubernetesJobClient, settings: Settings, spec: WorkerJobSpec
    ) -> None:
        self.client = client
        self.settings = settings
        self.spec = spec

    async def reconcile(self) -> bool:
        """One pass; True while there is build work to follow closely."""
        # Existing ownership is reconciled before admitting more remote work.
        returned: list[PlatformJob] = []
        async with get_db_context() as db:
            # Settings -> Kubernetes, read every pass.
            policy = await load_kubernetes_policy(db)
            if not policy.isolated_jobs_enabled:
                returned = await _return_unplaced_to_local(db)
                await db.commit()
            ids = (await db.scalars(select(PlatformJob.id).where(
                PlatformJob.execution_backend == "kubernetes",
                PlatformJob.kubernetes_job_name.is_not(None),
            ))).all()
        for job in returned:
            await publish_platform_job_update(job)
        for job_id in ids:
            await self.reconcile_job(job_id)
        if not policy.isolated_jobs_enabled:
            return bool(ids or returned)
        async with get_db_context() as db:
            sizing = await load_job_sizing(db, policy, self.settings.memory_pressure_threshold)
        standard_request = int(self.settings.pool_worker_memory_request_mib) * MIB
        if standard_request > sizing.standard_limit_bytes:
            # Nothing is claimed with a request no standard pod can make.
            raise KubernetesConfigurationError(
                "pool_worker_memory_request_mib must not exceed Worker memory"
            )
        claim = await claim_platform_job(
            backend="kubernetes",
            isolated=IsolatedPods(
                namespace=self.client.namespace,
                max_pods=policy.max_isolated_pods,
                sizing=sizing,
                standard_request_bytes=standard_request,
            ),
        )
        if claim is not None:
            await self.reconcile_job(claim.id)
        return bool(ids) or claim is not None

    async def reconcile_job(self, job_id: UUID) -> None:
        changed: PlatformJob | None = None
        loss: tuple[UUID, str, str] | None = None
        # Kubernetes stopped the pod at its memory limit.
        out_of_memory = False
        # The row lock serializes controllers. API calls are bounded by the
        # client's request timeout; remote admission cannot precede UID commit.
        async with get_db_context() as db:
            row = (await db.execute(select(PlatformJob).where(
                PlatformJob.id == job_id,
                PlatformJob.execution_backend == "kubernetes",
            ).with_for_update(skip_locked=True))).scalar_one_or_none()
            if row is None or not row.kubernetes_job_name:
                return
            if row.kubernetes_namespace != self.client.namespace:
                raise RuntimeError("Build namespace changed before existing attempts were drained and cleaned up")
            name = row.kubernetes_job_name
            remote = await self.client.get_job(name)
            if remote is not None and row.kubernetes_job_uid:
                if remote["metadata"]["uid"] != row.kubernetes_job_uid:
                    # Never adopt/delete a replacement that reused the name.
                    raise RuntimeError("Kubernetes build Job UID does not match its durable owner")
            if row.status not in _ACTIVE:
                if row.kubernetes_job_uid:
                    pods = await self.client.list_pods(row.kubernetes_job_uid)
                    if not _pods_stopped(pods):
                        return
                    if remote is not None:
                        await self.client.delete_job(name, row.kubernetes_job_uid)
                        return  # Confirm deletion on a subsequent API read.
                row.kubernetes_job_name = None
                return
            token = row.lease_token
            if token is None:
                raise RuntimeError("Active Kubernetes build has no attempt token")
            metadata = get_platform_job_metadata(row.job_type)
            if metadata is None:
                raise RuntimeError("Active Kubernetes build has no registered handler")
            claim = ClaimedPlatformJob(
                id=row.id, lease_token=token, timeout_seconds=row.timeout_seconds,
                hard_memory_ratio=metadata.policy.hard_memory_ratio,
                memory_required_bytes=row.memory_required_bytes,
                job_type=row.job_type,
            )
            if row.memory_limit_bytes is None:
                raise RuntimeError("Active Kubernetes build has no memory limit")
            manifest = build_job_manifest(
                claim,
                self.settings,
                self.spec,
                # An older release claimed without recording the request; the
                # migration's rule for such rows applies: the request is the limit.
                memory_request_bytes=row.memory_request_bytes or row.memory_limit_bytes,
                memory_limit_bytes=row.memory_limit_bytes,
            )
            if remote is not None:
                expected = manifest["metadata"]["labels"]
                actual = remote["metadata"].get("labels", {})
                if any(actual.get(key) != value for key, value in expected.items()):
                    raise RuntimeError("Kubernetes build Job labels do not match its durable owner")
            launch = row.kubernetes_launch_started_at
            if launch is None:
                raise RuntimeError("Kubernetes build has no launch timestamp")
            pending_expired = row.runner_started_at is None and (
                _now() - launch
            ).total_seconds() >= self.settings.kubernetes_job_pending_timeout_seconds
            if remote is None and row.kubernetes_job_uid is None:
                if pending_expired or row.status == "cancel_requested":
                    loss = (token, "capacity_unavailable", "A build pod could not start before the capacity deadline.")
                else:
                    # If create succeeds but its reply/DB commit is lost, the
                    # next pass reads/adopts this deterministic suspended Job.
                    remote = await self.client.create_job(manifest)
            if remote is not None and row.kubernetes_job_uid is None:
                row.kubernetes_job_uid = remote["metadata"]["uid"]
                await db.commit()
                return  # Activation happens only after the binding is durable.
            uid = row.kubernetes_job_uid
            if uid:
                pods = await self.client.list_pods(uid)
                # Kubernetes stopped the pod at its memory limit. That is
                # learned whatever else the attempt saw (an expired lease, a
                # removed Job): its runner stopped renewing because it died.
                out_of_memory = _pods_stopped(pods) and oom_killed(pods)
                if out_of_memory:
                    loss = (token, *_OUT_OF_MEMORY)
                elif remote is None:
                    if _pods_stopped(pods):
                        loss = (token, row.error_code or "runner_lost", row.error_message or "The Kubernetes build Job was removed before recording a result.")
                elif row.status == "cancel_requested" or pending_expired:
                    if row.status != "cancel_requested":
                        row.error_code = "capacity_unavailable"
                        row.error_message = "A build pod could not start before the capacity deadline."
                    await self.client.delete_job(name, uid)
                elif row.runner_started_at is not None and row.lease_expires_at is not None and row.lease_expires_at <= _now():
                    # Do not renew on behalf of an unresponsive runner. Stop its
                    # bound Job, and wait for observed pod termination first.
                    row.error_code = "runner_lost"
                    row.error_message = "The build runner stopped renewing its execution lease."
                    await self.client.delete_job(name, uid)
                elif remote.get("spec", {}).get("suspend"):
                    await self.client.start_job(name, uid)
                elif _pods_stopped(pods) and (pods or any(
                    c.get("status") == "True" and c.get("type") in ("Complete", "Failed")
                    for c in remote.get("status", {}).get("conditions", [])
                )):
                    loss = (token, *_failure(remote, pods))
                elif row.runner_started_at is None:
                    phase = _pending_phase(pods)
                    if row.phase != phase:
                        row.phase = phase
                        row.revision += 1
                        changed = row
            await db.commit()
        if changed is not None:
            await publish_platform_job_update(changed)
        if loss is not None:
            token, code, message = loss
            await _handle_runner_loss(
                job_id,
                token,
                error_code=code,
                error_message=message,
                out_of_memory=out_of_memory,
            )


async def kubernetes_build_loop(shutdown_event: asyncio.Event) -> None:
    settings = get_settings()
    try:
        client = KubernetesJobClient(settings)
    except KubernetesConfigurationError as exc:
        # Not in a cluster (wrong environment, missing token mount): the
        # host process must not go down. Fixing this needs a restart.
        logger.error("Kubernetes build controller disabled: %s", exc)
        await shutdown_event.wait()
        return
    try:
        # Isolated jobs wait visibly while local work continues.
        spec = await resolve_job_spec_when_ready(
            client, settings, shutdown_event, "Kubernetes build controller"
        )
        if spec is None:
            return
        controller = KubernetesBuildController(client, settings, spec)
        wake = asyncio.Event()
        listener = asyncio.create_task(
            listen_for_pool_wake(settings.redis_url, wake), name="kubernetes-build-wake"
        )
        try:
            while not shutdown_event.is_set():
                started = time.monotonic()
                busy = True
                try:
                    busy = await controller.reconcile()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Client exceptions never include tokens or API response bodies.
                    logger.error("Kubernetes build reconciliation failed (%s): %s", type(exc).__name__, exc)
                if busy:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(shutdown_event.wait(), timeout=BUILD_POLL_SECONDS)
                else:
                    await wait_for_next_pass(
                        shutdown_event, wake, since=started,
                        spacing=BUILD_POLL_SECONDS, sweep=BUILD_SWEEP_SECONDS,
                    )
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
    finally:
        # Scheduler shutdown does not terminate remotely supervised builds.
        await client.close()
