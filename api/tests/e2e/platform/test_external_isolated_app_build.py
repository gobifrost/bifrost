"""One local external-isolated App build through the ordinary launcher.

This proves the normal worker-image launcher subprocess can perform an
API-owned ``ApiFileComputeBackend`` ``app.compile.v2`` stage and promote its
artifacts through the external-isolated claim path. It does *not* prove
Cloudflare placement, provider live conformance, or performance characteristics.

Cost: one ``application.deploy`` PlatformJob. Existing thin-worker journeys
cover legacy publish and SDK rebuilds; this case exclusively covers the
external resource reservation → normal Launcher → API file-compute boundary.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from shared.version import get_version
from shared.worker_control_auth import external_worker_control_key
from src.core.security import encrypt_secret
from src.models.orm.applications import Application
from src.models.orm.config import SystemConfig
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.platform_jobs import PlatformJob
from src.services.application_source_artifact import ApplicationSourceArtifactStorage
from src.services.execution.memory_monitor import effective_memory_sample
from src.services.kubernetes_execution import (
    KUBERNETES_CONFIG_CATEGORY,
    KUBERNETES_CONFIG_KEY,
)
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.worker_pool.connection import CATEGORY, KEY
from tests.e2e.platform.test_thin_platform_jobs_build import _vite_source_zip
from tests.e2e.platform.thin_live_helpers import wait_for_job


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_CAPACITY_FIXTURE_URL = "https://fixture.openrouter.ai:8443"


def _capacity_fixture_request(
    method: str,
    path: str,
    *,
    token: str,
    payload=None,
    scope: str | None = None,
) -> httpx.Response:
    """Use the test-only TLS control plane without inheriting host proxies."""
    with httpx.Client(
        base_url=_CAPACITY_FIXTURE_URL,
        verify=ssl.create_default_context(cafile="/fixture-tls/trust.pem"),
        trust_env=False,
        timeout=10.0,
    ) as client:
        headers = {"Authorization": f"Bearer {token}"}
        if scope is not None:
            headers["X-Bifrost-Capacity-Scope"] = scope
        return client.request(
            method,
            path,
            headers=headers,
            json=payload,
        )


async def _absent_config(async_session_factory, category: str, key: str) -> None:
    async with async_session_factory() as db:
        existing = await db.scalar(
            select(SystemConfig).where(
                SystemConfig.category == category,
                SystemConfig.key == key,
                SystemConfig.organization_id.is_(None),
            )
        )
    if existing is not None:
        pytest.fail(f"external isolated E2E requires an empty {category}/{key} config")


async def test_external_isolated_launcher_promotes_one_app_deployment(
    e2e_client, e2e_api_url, platform_admin, async_session_factory
) -> None:
    await _absent_config(async_session_factory, CATEGORY, KEY)
    await _absent_config(
        async_session_factory, KUBERNETES_CONFIG_CATEGORY, KUBERNETES_CONFIG_KEY
    )
    suffix = uuid.uuid4().hex[:10]
    scope = f"e2e-external-{suffix}"
    resource_uid = f"resource-{suffix}"
    provider_token = f"fixture-capacity-token-{uuid.uuid4().hex}"
    generation = uuid.uuid4()
    worker_id = uuid.uuid4()
    _, launcher_memory_limit = effective_memory_sample()
    # The provider contract uses whole MiB. An unbounded local container can
    # report the host's non-aligned MemTotal; advertise its conservative whole
    # MiB floor. This fixture proves execution, not hardware conformance.
    launcher_memory_mib = launcher_memory_limit // (1024 * 1024)
    launcher_memory_limit = launcher_memory_mib * 1024 * 1024
    app_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    process: subprocess.Popen[bytes] | None = None
    fixture_configured = False
    try:
        fixture = _capacity_fixture_request(
            "POST",
            "/fixture/capacity",
            token=provider_token,
            payload={
                "scope": scope,
                "token": provider_token,
                "worker_id": str(worker_id),
                "resource_uid": resource_uid,
                "worker_version": get_version(),
                "memory_limit_bytes": launcher_memory_limit,
            },
        )
        assert fixture.status_code == 201, fixture.text
        fixture_configured = True
        preseeded = _capacity_fixture_request(
            "GET",
            f"/capacity/{scope}/v1/workers/{worker_id}",
            token=provider_token,
            scope=scope,
        )
        assert preseeded.status_code == 200, preseeded.text
        assert preseeded.json() == {
            "scope": scope,
            "worker_id": str(worker_id),
            "worker_version": get_version(),
            "resource_uid": resource_uid,
            "state": "running",
            "out_of_memory": False,
        }
        async with async_session_factory() as db:
            db.add(
                SystemConfig(
                    category=CATEGORY,
                    key=KEY,
                    organization_id=None,
                    created_by="external-isolated-e2e",
                    value_json={
                        "generation": str(generation),
                        "active_generation": str(generation),
                        "scope": scope,
                        "endpoint": f"{_CAPACITY_FIXTURE_URL}/capacity/{scope}",
                        "account_id": "e2e-account",
                        "enabled": True,
                        "status": "ready",
                        "rules": {"app_builds": True, "prefer_kubernetes": False},
                    },
                    value_bytes=encrypt_secret(
                        json.dumps(
                            {
                                "version": 1,
                                "api_key": "e2e-provider-api-key",
                                "capacity_token": provider_token,
                            }
                        )
                    ).encode("ascii"),
                )
            )
            db.add(
                SystemConfig(
                    category=KUBERNETES_CONFIG_CATEGORY,
                    key=KUBERNETES_CONFIG_KEY,
                    organization_id=None,
                    created_by="external-isolated-e2e",
                    value_json={
                        "warm_workers": 0,
                        "max_workers": 1,
                        "worker_memory_mib": launcher_memory_mib,
                        "largest_worker_mib": launcher_memory_mib,
                        "isolated_jobs_enabled": True,
                        "enabled_job_types": ["application.deploy"],
                    },
                )
            )
            await db.commit()

        headers = platform_admin.headers
        created = e2e_client.post(
            "/api/applications",
            headers=headers,
            json={
                "name": f"External Isolated {suffix}",
                "slug": f"external-isolated-{suffix}",
                "app_model": "standalone_v2",
            },
        )
        assert created.status_code == 201, created.text
        app_id = uuid.UUID(created.json()["id"])
        upload_headers = {
            key: value for key, value in headers.items() if key.lower() != "content-type"
        }
        enqueued = e2e_client.post(
            f"/api/applications/{app_id}/deploy",
            headers=upload_headers,
            files={"source": ("source.zip", _vite_source_zip(), "application/zip")},
        )
        assert enqueued.status_code == 202, enqueued.text
        job_id = uuid.UUID(enqueued.json()["job_id"])

        async with async_session_factory() as db:
            job = await db.get(PlatformJob, job_id)
            assert job is not None
            assert job.execution_backend == "external_isolated"
            db.add(
                ExecutionWorker(
                    id=worker_id,
                    form="external",
                    state="starting",
                    resource_class="isolated",
                    execution_lane="normal",
                    capacity_scope=scope,
                    capacity_resource_uid=resource_uid,
                    version=get_version(),
                    supported_handlers=[],
                    supported_compute=["app.compile.v2"],
                    # This exact process will be the launcher below, so reserve
                    # its measured effective limit rather than fabricating a
                    # provider resource shape.
                    memory_limit_bytes=launcher_memory_limit,
                    isolated_platform_job_id=job_id,
                    isolated_platform_job_attempt=job.attempt + 1,
                )
            )
            await db.commit()

        environment = {
            "PATH": os.environ["PATH"],
            "PYTHONPATH": os.environ.get("PYTHONPATH", "/app"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "BIFROST_API_URL": e2e_api_url,
            "BIFROST_WORKER_FORM": "external",
            "BIFROST_WORKER_INCARNATION_ID": str(worker_id),
            "BIFROST_WORKER_RESOURCE_UID": resource_uid,
            "BIFROST_WORKER_CONTROL_SECRET": external_worker_control_key(
                provider_token, worker_id, resource_uid
            ),
            "HOSTNAME": f"e2e-external-{suffix}",
        }
        process = subprocess.Popen(
            [sys.executable, "-m", "src.worker.remote.launcher"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        job = wait_for_job(e2e_client, headers, job_id, "application.deploy", max_wait=120)
        assert job["attempt"] == 1

        async with async_session_factory() as db:
            persisted = await db.get(PlatformJob, job_id)
            app = await db.get(Application, app_id)
            assert persisted is not None and app is not None
            assert persisted.executor_worker_id == worker_id
            assert persisted.executor_attempt == 1
            assert persisted.executor_capacity_scope == scope
            assert persisted.executor_capacity_resource_uid == resource_uid
            assert persisted.executor_compute_capability == "app.compile.v2"
            assert app.active_deployment_id is not None
            deployment_id = app.active_deployment_id
        source = e2e_client.get(f"/api/applications/{app_id}/source", headers=headers)
        assert source.status_code == 200, source.text
        assert b"src/main.ts" in source.content
        assert await ApplicationSourceArtifactStorage().deployment_source_exists(
            app_id, deployment_id
        )
        index_html = await SolutionAppBuilder().read_dist(
            app_id, "index.html", deployment_id=deployment_id
        )
        assert b"assets/" in index_html
        artifact = e2e_client.get(
            f"/api/applications/{app_id}/dist/index.html", headers=headers
        )
        assert artifact.status_code == 200, artifact.text
        assert artifact.content == index_html
        assert process.wait(timeout=60) == 0
        async with async_session_factory() as db:
            worker = await db.get(ExecutionWorker, worker_id)
            persisted = await db.get(PlatformJob, job_id)
            assert worker is not None and worker.state == "retired"
            assert list(await db.scalars(select(PlatformJob.id).where(
                PlatformJob.executor_worker_id == worker_id,
            ))) == [job_id]
            assert persisted is not None and persisted.attempt == 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=20)
        async with async_session_factory() as db:
            if job_id is not None:
                await db.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await db.execute(
                delete(SystemConfig).where(
                    SystemConfig.category == CATEGORY,
                    SystemConfig.key == KEY,
                    SystemConfig.organization_id.is_(None),
                    SystemConfig.created_by == "external-isolated-e2e",
                )
            )
            await db.execute(
                delete(SystemConfig).where(
                    SystemConfig.category == KUBERNETES_CONFIG_CATEGORY,
                    SystemConfig.key == KUBERNETES_CONFIG_KEY,
                    SystemConfig.organization_id.is_(None),
                    SystemConfig.created_by == "external-isolated-e2e",
                )
            )
            await db.commit()
        if app_id is not None:
            deleted = e2e_client.delete(
                f"/api/applications/{app_id}", headers=platform_admin.headers
            )
            assert deleted.status_code == 204, deleted.text
        if fixture_configured:
            fixture = _capacity_fixture_request(
                "DELETE",
                f"/fixture/capacity/{scope}/{worker_id}",
                token=provider_token,
            )
            assert fixture.status_code == 204, fixture.text
