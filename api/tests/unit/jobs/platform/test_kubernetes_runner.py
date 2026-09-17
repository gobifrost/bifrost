from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform import kubernetes_runner
from src.jobs.platform.kubernetes_client import _lease_token_fingerprint
from src.jobs.platform.application_deploy import (
    APPLICATION_DEPLOY_DEFINITION,
    ApplicationDeployPayload,
)
from src.jobs.schedulers import platform_jobs as scheduler
from src.services.platform_jobs import enqueue_platform_job


@pytest.fixture(autouse=True)
def patch_context(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(kubernetes_runner, "get_db_context", test_context)
    monkeypatch.setattr(
        kubernetes_runner,
        "publish_platform_job_update",
        AsyncMock(),
    )
    monkeypatch.setattr(kubernetes_runner, "get_cgroup_memory", lambda: (128, 1024))


@pytest.mark.asyncio
async def test_admit_kubernetes_platform_job_binds_first_pod(
    db_session: AsyncSession,
) -> None:
    app_id = uuid4()
    job, _ = await enqueue_platform_job(
        db_session,
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
        title="Deploying Test",
        action_url=None,
    )
    token = uuid4()
    job.execution_backend = "kubernetes"
    job.status = "running"
    job.phase = "Waiting for Kubernetes capacity"
    job.lease_token = token
    job.lease_owner = "kubernetes-controller"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    job.kubernetes_job_name = (
        f"bifrost-job-{job.id.hex}-{_lease_token_fingerprint(token)[:12]}"
    )
    job.kubernetes_job_uid = "job-uid"
    await db_session.commit()

    claim = await kubernetes_runner.admit_kubernetes_platform_job(
        job.id,
        token,
        kubernetes_job_uid="job-uid",
        kubernetes_pod_uid="pod-uid",
    )

    assert claim is not None
    assert claim.id == job.id
    assert claim.lease_token == token
    assert claim.job_type == "application.deploy"
    assert claim.hard_memory_ratio == scheduler.get_platform_job_definition(
        "application.deploy"
    ).policy.hard_memory_ratio
    await db_session.refresh(job)
    assert job.kubernetes_pod_uid == "pod-uid"
    assert job.lease_owner == "kubernetes-pod:pod-uid"
    assert job.runner_started_at is not None
    assert job.phase == "Starting"
    assert job.memory_start_bytes == 128
    assert job.memory_peak_bytes == 128
    assert job.memory_limit_bytes == 1024


@pytest.mark.asyncio
async def test_admit_kubernetes_platform_job_rejects_wrong_uid(
    db_session: AsyncSession,
) -> None:
    app_id = uuid4()
    job, _ = await enqueue_platform_job(
        db_session,
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
        title="Deploying Test",
        action_url=None,
    )
    token = uuid4()
    job.execution_backend = "kubernetes"
    job.status = "running"
    job.lease_token = token
    job.kubernetes_job_uid = "job-uid"
    await db_session.commit()

    claim = await kubernetes_runner.admit_kubernetes_platform_job(
        job.id,
        token,
        kubernetes_job_uid="different-job-uid",
        kubernetes_pod_uid="pod-uid",
    )

    assert claim is None
    await db_session.refresh(job)
    assert job.kubernetes_pod_uid is None


@pytest.mark.asyncio
@pytest.mark.parametrize("pod_uid", ["pod-uid", "other-pod-uid"])
async def test_admit_kubernetes_platform_job_rejects_duplicate_pod_binding(
    db_session: AsyncSession,
    pod_uid: str,
) -> None:
    app_id = uuid4()
    job, _ = await enqueue_platform_job(
        db_session,
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
        title="Deploying Test",
        action_url=None,
    )
    token = uuid4()
    job.execution_backend = "kubernetes"
    job.status = "running"
    job.lease_token = token
    job.kubernetes_job_uid = "job-uid"
    job.kubernetes_pod_uid = "pod-uid"
    await db_session.commit()

    claim = await kubernetes_runner.admit_kubernetes_platform_job(
        job.id,
        token,
        kubernetes_job_uid="job-uid",
        kubernetes_pod_uid=pod_uid,
    )

    assert claim is None
    await db_session.refresh(job)
    assert job.kubernetes_pod_uid == "pod-uid"


@pytest.mark.asyncio
async def test_admit_kubernetes_platform_job_rejects_persisted_stop_intent(
    db_session: AsyncSession,
) -> None:
    app_id = uuid4()
    job, _ = await enqueue_platform_job(
        db_session,
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
        title="Deploying Test",
        action_url=None,
    )
    token = uuid4()
    job.execution_backend = "kubernetes"
    job.status = "running"
    job.lease_token = token
    job.kubernetes_job_uid = "job-uid"
    # A stop intent the controller persists on a still-running row (e.g. the
    # capacity deadline firing while the Job still exists).
    job.error_code = "capacity_unavailable"
    await db_session.commit()

    claim = await kubernetes_runner.admit_kubernetes_platform_job(
        job.id,
        token,
        kubernetes_job_uid="job-uid",
        kubernetes_pod_uid="pod-uid",
    )

    assert claim is None
    await db_session.refresh(job)
    assert job.kubernetes_pod_uid is None
