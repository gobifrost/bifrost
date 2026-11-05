"""Concurrent control replicas launch each Kubernetes build attempt once.

Every API control replica runs the build reconciler. This drives two
controllers at the same queue against real PostgreSQL row locks and a fake
cluster that interleaves at every API call. Turning isolated jobs off stops
admitting, keeps reconciling the pods already placed, and moves queued
isolated jobs to the local backend.
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.jobs.platform.catalog import get_platform_job_metadata
from src.jobs.platform.application_deploy import (
    APPLICATION_DEPLOY_DEFINITION,
    ApplicationDeployPayload,
)
from src.jobs.schedulers import kubernetes_jobs, platform_jobs
from src.models.orm.config import SystemConfig
from src.models.orm.platform_jobs import PlatformJob
from src.services.kubernetes_execution import (
    KUBERNETES_CONFIG_CATEGORY,
    KUBERNETES_CONFIG_KEY,
    KubernetesExecutionService,
)
from src.services.platform_jobs import enqueue_platform_job
from src.services.worker_pool.manifest import WorkerJobSpec

# Max isolated pods: the Settings -> Kubernetes default.
MAX_JOBS = 2
QUEUED = 5

SETTINGS = SimpleNamespace(
    pool_worker_memory_request_mib=512,
    kubernetes_job_cpu_request="250m",
    kubernetes_job_cpu_limit="1",
    kubernetes_job_pending_timeout_seconds=300,
    memory_pressure_threshold=0.85,
)
SPEC = WorkerJobSpec(
    namespace="test",
    image="api:test",
    env_from=({"configMapRef": {"name": "config"}}, {"secretRef": {"name": "secret"}}),
    service_account="runner",
)


class FakeCluster:
    """A namespace whose API yields between every request, like a network."""

    namespace = "test"

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.created: list[str] = []
        self.started: list[str] = []
        self.read: list[str] = []

    async def get_job(self, name: str) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        self.read.append(name)
        job = self.jobs.get(name)
        return copy.deepcopy(job) if job else None

    async def create_job(self, manifest: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        name = manifest["metadata"]["name"]
        if name not in self.jobs:  # 409 is answered with the existing Job
            job = copy.deepcopy(manifest)
            job["metadata"]["uid"] = str(uuid4())
            self.jobs[name] = job
            self.created.append(name)
        return copy.deepcopy(self.jobs[name])

    async def start_job(self, name: str, uid: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        job = self.jobs[name]
        assert job["metadata"]["uid"] == uid
        if job["spec"]["suspend"]:
            job["spec"]["suspend"] = False
            self.started.append(name)
        return copy.deepcopy(job)

    async def list_pods(self, _job_uid: str) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        return []

    async def delete_job(self, name: str, uid: str) -> None:
        raise AssertionError(f"No Job should be deleted ({name}, {uid})")


@pytest.fixture
def real_sessions(
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> async_sessionmaker[AsyncSession]:
    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session
            await session.commit()

    for module in (platform_jobs, kubernetes_jobs):
        monkeypatch.setattr(module, "get_db_context", context)
        monkeypatch.setattr(module, "publish_platform_job_update", AsyncMock())
    # Production serializes deploys per type; lift it so the global remote
    # ceiling is the only limit the two controllers contend for.
    unlimited = replace(
        APPLICATION_DEPLOY_DEFINITION,
        policy=replace(APPLICATION_DEPLOY_DEFINITION.policy, max_concurrency=None),
    )
    metadata = get_platform_job_metadata(unlimited.job_type)
    assert metadata is not None
    unlimited_metadata = replace(metadata, policy=unlimited.policy)

    def metadata_lookup(job_type: str):
        if job_type == unlimited.job_type:
            return unlimited_metadata
        return get_platform_job_metadata(job_type)

    monkeypatch.setattr(
        platform_jobs, "get_platform_job_metadata",
        metadata_lookup,
    )
    monkeypatch.setattr(kubernetes_jobs, "get_platform_job_metadata", metadata_lookup)
    return async_session_factory


async def _isolated_jobs(sessions: async_sessionmaker[AsyncSession], enabled: bool) -> None:
    """What an admin saves in Settings -> Kubernetes."""
    async with sessions() as session:
        await KubernetesExecutionService(session).update_policy(
            {"isolated_jobs_enabled": enabled}, updated_by="test"
        )
        await session.commit()


@pytest_asyncio.fixture
async def isolation_on(
    real_sessions: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[None, None]:
    await _isolated_jobs(real_sessions, True)
    yield
    async with real_sessions() as session:
        await session.execute(delete(PlatformJob))
        await session.execute(
            delete(SystemConfig).where(
                SystemConfig.category == KUBERNETES_CONFIG_CATEGORY,
                SystemConfig.key == KUBERNETES_CONFIG_KEY,
            )
        )
        await session.commit()


async def _queue_remote_builds(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        await session.execute(delete(PlatformJob))
        for index in range(QUEUED):
            app_id = uuid4()
            job, _ = await enqueue_platform_job(
                session,
                APPLICATION_DEPLOY_DEFINITION,
                ApplicationDeployPayload(
                    application_id=app_id,
                    deployment_id=uuid4(),
                    input_sha256="a" * 64,
                ),
                dedupe_key=str(app_id),
                organization_id=None,
                requested_by_user_id=uuid4(),
                requested_by_email="dev@example.com",
                requested_by_name="Dev",
                resource_type="application",
                resource_id=str(app_id),
                title=f"Deploying {index}",
                action_url=None,
            )
            job.execution_backend = "kubernetes"
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_controllers_launch_each_attempt_once(
    real_sessions: async_sessionmaker[AsyncSession], isolation_on: None
) -> None:
    await _queue_remote_builds(real_sessions)
    cluster = FakeCluster()
    replicas = [
        kubernetes_jobs.KubernetesBuildController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]
        for _ in range(2)
    ]

    for _ in range(6):
        await asyncio.gather(*(replica.reconcile() for replica in replicas))

    async with real_sessions() as session:
        rows = (await session.execute(select(PlatformJob))).scalars().all()
    running = [row for row in rows if row.status == "running"]

    assert len(running) == MAX_JOBS
    assert all(row.attempt == 1 for row in running)
    assert all(row.status == "queued" and row.attempt == 0 for row in rows if row not in running)
    # One Job per admitted attempt, bound to the UID the cluster returned and
    # started exactly once.
    assert sorted(cluster.created) == sorted(row.kubernetes_job_name for row in running)
    assert sorted(cluster.started) == sorted(cluster.created)
    for row in running:
        assert row.kubernetes_job_uid == cluster.jobs[row.kubernetes_job_name]["metadata"]["uid"]


@pytest.mark.asyncio
async def test_turning_isolation_off_finishes_placed_pods_and_moves_queued_jobs_to_workers(
    real_sessions: async_sessionmaker[AsyncSession], isolation_on: None
) -> None:
    await _queue_remote_builds(real_sessions)
    cluster = FakeCluster()
    controller = kubernetes_jobs.KubernetesBuildController(cluster, SETTINGS, SPEC)  # type: ignore[arg-type]
    for _ in range(3):
        await controller.reconcile()
    assert len(cluster.started) == MAX_JOBS
    async with real_sessions() as session:
        # A queued job a release before this one parked on the old wait.
        await session.execute(
            update(PlatformJob)
            .where(PlatformJob.status == "queued")
            .values(phase="Waiting for Kubernetes capacity")
        )
        await session.commit()

    await _isolated_jobs(real_sessions, False)
    cluster.read.clear()
    await controller.reconcile()

    async with real_sessions() as session:
        rows = (await session.execute(select(PlatformJob))).scalars().all()
    running = [row for row in rows if row.status == "running"]
    queued = [row for row in rows if row.status == "queued"]
    # Placed pods are still reconciled until they end; none is added.
    assert sorted(cluster.read) == sorted(row.kubernetes_job_name for row in running)
    assert len(cluster.created) == MAX_JOBS
    assert all(row.execution_backend == "kubernetes" for row in running)
    # Queued jobs run on a worker, admitted by memory like any local job.
    assert len(queued) == QUEUED - MAX_JOBS
    assert all(
        (row.execution_backend, row.phase, row.kubernetes_job_name) == ("local", "Queued", None)
        for row in queued
    )
