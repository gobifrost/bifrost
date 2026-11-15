"""Database fences at isolated Kubernetes → shared API compute admission.

No physical jobs: refusal/cleanup cases use the existing DB boundary and a fake
Kubernetes API. The separate normal-launcher journey covers physical compute.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.jobs.platform import kubernetes_compute as module
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.services.worker_pool.claims import admit_worker
from src.worker.remote.fencing import LeaseRef, remote_owner_context
from src.worker.remote.ownership import RemoteOwnerLease
from src.worker.remote.protocol import AuthenticationError

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
async def reservation(async_session_factory, monkeypatch):
    @asynccontextmanager
    async def context():
        async with async_session_factory() as db:
            yield db
            await db.commit()

    monkeypatch.setattr(module, "get_db_context", context)
    monkeypatch.setattr(module, "get_liveness_db_context", context)
    monkeypatch.setattr(module, "publish_platform_job_update", AsyncMock())
    token, job_id, owner, launcher = uuid4(), uuid4(), uuid4(), uuid4()
    worker_id = module.compute_worker_id(token)
    now = datetime.now(timezone.utc)
    async with context() as db:
        db.add(PlatformJob(id=job_id, job_type="solution.export", payload={}, status="running",
            execution_backend="kubernetes", attempt=1, lease_token=token,
            requested_by_user_id=str(uuid4()), requested_by_name="Fixture",
            requested_by_email="fixture@example.test", title="Fixture",
            memory_required_bytes=64, kubernetes_namespace="test", kubernetes_job_name=f"job-{job_id}",
            kubernetes_job_uid="job-uid", lease_expires_at=now + timedelta(seconds=30)))
        db.add(ExecutionWorker(id=worker_id, form="job", state="reserved", resource_class="isolated",
            execution_lane="normal", supported_handlers=[], supported_compute=["solution.archive.inspect.v1"],
            isolated_platform_job_id=job_id, isolated_platform_job_attempt=1,
            kubernetes_namespace="test", kubernetes_job_name=f"job-{job_id}",
            remote_owner_id=owner, remote_launcher_id=launcher, remote_session_generation=1,
            remote_owner_lease_expires_at=now + timedelta(seconds=30)))
    lease = LeaseRef(RemoteOwnerLease(worker_id, owner, launcher, 1))
    try:
        yield job_id, token, worker_id, lease, context
    finally:
        async with context() as db:
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))


async def _ready(reservation):
    job_id, _token, worker_id, _lease, _context = reservation
    await module.sync_compute_worker(job_id)
    assert await admit_worker(worker_id, job_uid="different-job", pod_uid="pod", version="fixture") is None
    assert await admit_worker(worker_id, job_uid="job-uid", pod_uid="pod", version="fixture") == worker_id


async def _adopt(reservation, *, attempt=1, fits=128):
    job_id, _token, worker_id, lease, _context = reservation
    with remote_owner_context(lease):
        return await module.adopt_compute_claim(worker_id, job_id, attempt, fits_bytes=fits,
            memory_sample=(16, 1024), job_types=frozenset({"solution.export"}))


async def test_compute_pod_adopts_only_exact_preclaim_once(reservation):
    job_id, token, worker_id, _lease, context = reservation
    await _ready(reservation)
    assert await _adopt(reservation, attempt=2) is None
    assert await _adopt(reservation, fits=32) is None
    claim = await _adopt(reservation)
    assert claim is not None and claim.id == job_id and claim.lease_token == token
    assert await _adopt(reservation) is None
    async with context() as db:
        job = await db.get(PlatformJob, job_id)
        assert job.attempt == 1 and job.executor_worker_id == worker_id
        assert job.executor_worker_form == "job" and job.kubernetes_pod_uid == "pod"


@pytest.mark.parametrize("refusal", ["cancelled", "owner_lost", "missing_compute", "retry"])
async def test_adoption_refuses_stale_or_unfit_authority(reservation, refusal):
    job_id, _token, worker_id, _lease, context = reservation
    await _ready(reservation)
    async with context() as db:
        job = await db.get(PlatformJob, job_id)
        worker = await db.get(ExecutionWorker, worker_id)
        if refusal == "cancelled":
            job.status = "cancel_requested"
        elif refusal == "owner_lost":
            worker.remote_session_generation = 2
        elif refusal == "missing_compute":
            worker.supported_compute = []
        else:
            job.lease_token = uuid4()
    assert await _adopt(reservation) is None


async def test_attempt_scoped_key_revoked_when_job_advances(reservation):
    job_id, token, worker_id, _lease, context = reservation
    await _ready(reservation)
    key = await module.compute_control_key(worker_id, "root-fixture")
    assert key is not None and key != "root-fixture" and key != str(token)
    async with context() as db:
        job = await db.get(PlatformJob, job_id)
        job.attempt += 1
        job.lease_token = uuid4()
    with pytest.raises(AuthenticationError, match="no longer admissible"):
        await module.compute_control_key(worker_id, "root-fixture")


async def test_cleanup_waits_for_uid_bound_pod_termination(reservation):
    job_id, _token, worker_id, _lease, context = reservation
    await _ready(reservation)
    async with context() as db:
        job = await db.get(PlatformJob, job_id)
        job.status = "cancelled"
    client = AsyncMock()
    client.namespace = "test"
    client.get_job.return_value = {"metadata": {"uid": "job-uid"}}
    await module.cleanup_compute_workers(job_id, client)
    client.delete_job.assert_awaited_once()
    client.get_job.return_value = None
    client.list_pods.return_value = [{"status": {"phase": "Running"}}]
    await module.cleanup_compute_workers(job_id, client)
    async with context() as db:
        assert (await db.get(ExecutionWorker, worker_id)).state == "ready"
    client.list_pods.return_value = [{"status": {"phase": "Failed"}}]
    await module.cleanup_compute_workers(job_id, client)
    async with context() as db:
        assert (await db.get(ExecutionWorker, worker_id)).state == "lost"


async def test_cleanup_refuses_replacement_job_uid(reservation):
    job_id, _token, _worker_id, _lease, context = reservation
    await _ready(reservation)
    async with context() as db:
        job = await db.get(PlatformJob, job_id)
        job.status = "failed"
    client = AsyncMock()
    client.namespace = "test"
    client.get_job.return_value = {"metadata": {"uid": "replacement"}}
    with pytest.raises(RuntimeError, match="UID changed"):
        await module.cleanup_compute_workers(job_id, client)
    client.delete_job.assert_not_awaited()


async def test_cleanup_preserves_retirement_history(reservation):
    job_id, _token, worker_id, _lease, context = reservation
    await _ready(reservation)
    retired_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    async with context() as db:
        job = await db.get(PlatformJob, job_id)
        worker = await db.get(ExecutionWorker, worker_id)
        job.status = "succeeded"
        worker.state = "retired"
        worker.ended_at = retired_at
    client = AsyncMock()
    client.namespace = "test"
    client.get_job.return_value = None
    client.list_pods.return_value = []
    await module.cleanup_compute_workers(job_id, client)
    async with context() as db:
        worker = await db.get(ExecutionWorker, worker_id)
        assert worker.state == "retired" and worker.ended_at == retired_at
        assert worker.end_reason == "isolated_resource_deleted"


async def test_cleanup_commits_unbound_resource_uid_before_deleting(reservation):
    job_id, _token, worker_id, _lease, context = reservation
    async with context() as db:
        job = await db.get(PlatformJob, job_id)
        job.status = "cancelled"
    client = AsyncMock()
    client.namespace = "test"
    client.get_job.return_value = {"metadata": {"uid": "job-uid", "labels": {
        "bifrost.gobifrost.com/worker-incarnation": str(worker_id),
    }}}
    await module.cleanup_compute_workers(job_id, client)
    client.delete_job.assert_not_awaited()
    async with context() as db:
        assert (await db.get(ExecutionWorker, worker_id)).kubernetes_job_uid == "job-uid"
    await module.cleanup_compute_workers(job_id, client)
    client.delete_job.assert_awaited_once()
