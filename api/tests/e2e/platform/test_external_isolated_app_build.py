"""One local external-isolated App build through the ordinary launcher.

This proves the normal worker-image launcher subprocess can perform an
API-owned ``ApiFileComputeBackend`` ``app.compile.v2`` stage and promote its
artifacts through the external-isolated claim path. It does *not* prove
Cloudflare placement, provider live conformance, or performance characteristics.

Cost: one PlatformJob per case: an independent App deployment and a Solution
deployment, plus one durable export in the export case. The export proves API-only
crypto, authorized download and immutable attempt publication. The Solution case uniquely proves sequential inspect→compile→source-pack stages
under one exact isolated claim. Existing thin-worker journeys
cover legacy publish and SDK rebuilds; this case exclusively covers the
external resource reservation → normal Launcher → API file-compute boundary.
"""

from __future__ import annotations

import json
import io
import zipfile
import os
import ssl
import subprocess
import sys
import uuid
from unittest.mock import AsyncMock
from typing import Any

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
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.config import Config
from src.models.orm.solutions import Solution
from src.models.enums import ConfigType
from src.services.solutions.secrets_blob import decode_secrets_blob
from src.services.solutions.deploy import solution_entity_id
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage
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


@pytest.mark.parametrize("job_type,backend", [
    ("application.deploy", "external"), ("solution.deploy", "external"),
    ("solution.export", "external"), ("solution.export", "kubernetes"),
])
async def test_external_isolated_launcher_promotes_one_app_deployment(
    e2e_client, e2e_api_url, platform_admin, async_session_factory, job_type, backend, monkeypatch
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
    capacity_fixture_worker_id = worker_id
    _, launcher_memory_limit = effective_memory_sample()
    # The provider contract uses whole MiB. An unbounded local container can
    # report the host's non-aligned MemTotal; advertise its conservative whole
    # MiB floor. This fixture proves execution, not hardware conformance.
    launcher_memory_mib = launcher_memory_limit // (1024 * 1024)
    launcher_memory_limit = launcher_memory_mib * 1024 * 1024
    export_job_id: uuid.UUID | None = None
    exported_config_key = f"export_value_{suffix}"
    export_password = "fixture-export-password"
    solution_id: uuid.UUID | None = None
    solution_slug = f"external-solution-{suffix}"
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
                        "enabled_job_types": [job_type],
                    },
                )
            )
            await db.commit()

        headers = platform_admin.headers
        upload_headers = {
            key: value for key, value in headers.items() if key.lower() != "content-type"
        }
        if job_type == "application.deploy":
            created = e2e_client.post(
                "/api/applications", headers=headers,
                json={"name": f"External Isolated {suffix}",
                      "slug": f"external-isolated-{suffix}", "app_model": "standalone_v2"},
            )
            assert created.status_code == 201, created.text
            app_id = uuid.UUID(created.json()["id"])
            enqueued = e2e_client.post(
                f"/api/applications/{app_id}/deploy", headers=upload_headers,
                files={"source": ("source.zip", _vite_source_zip(), "application/zip")},
            )
        else:
            created = e2e_client.post(
                "/api/solutions", headers=headers,
                json={"name": f"External Solution {suffix}", "slug": solution_slug,
                      "organization_id": None},
            )
            assert created.status_code == 201, created.text
            solution_id = uuid.UUID(created.json()["id"])
            if job_type == "solution.export":
                async with async_session_factory() as db:
                    solution = await db.get(Solution, solution_id)
                    assert solution is not None
                    solution.readme = "Current backup README"
                    db.add(SolutionConfigSchema(solution_id=solution_id, key=exported_config_key, type="string"))
                    db.add(Config(key=exported_config_key, value={"value": "API-only backup value"},
                                  config_type=ConfigType.STRING, organization_id=None, updated_by="export-fixture"))
                    await db.commit()
                source = io.BytesIO()
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("bifrost.solution.yaml", f"slug: {solution_slug}\nname: Export Solution\n")
                    archive.writestr("modules/retained.py", "VALUE = 'stored source'\n")
                    archive.writestr("README.md", "Old README")
                    archive.writestr(".bifrost/secrets.enc", b"stale encrypted content")
                await SolutionSourceArtifactStorage(solution_id).write(source.getvalue())
                enqueued = e2e_client.post(
                    f"/api/solutions/{solution_id}/export-jobs", headers=headers,
                    json={"options": {"include_configs": True, "include_secrets": False,
                                     "include_tables": False, "include_files": False,
                                     "password": export_password}},
                )
            else:
                manifest_app_id = uuid.uuid4()
                app_id = solution_entity_id(solution_id, manifest_app_id)
                source = io.BytesIO()
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("bifrost.solution.yaml", f"slug: {solution_slug}\nname: External Solution\n")
                    archive.writestr(".bifrost/apps.yaml", json.dumps({"apps": {str(manifest_app_id): {
                        "name": "External Solution App", "slug": f"solution-app-{suffix}",
                        "path": "apps/demo", "app_model": "standalone_v2",
                        "dependencies": {"vite": "^6.2.2"},
                    }}}))
                    with zipfile.ZipFile(io.BytesIO(_vite_source_zip())) as app_source:
                        for info in app_source.infolist():
                            archive.writestr(f"apps/demo/{info.filename}", app_source.read(info))
                enqueued = e2e_client.post(
                    f"/api/solutions/{solution_id}/deploy", headers=upload_headers,
                    files={"file": ("solution.zip", source.getvalue(), "application/zip")},
                )
        assert enqueued.status_code == 202, enqueued.text
        if job_type == "solution.export":
            export_job_id = uuid.UUID(enqueued.json()["id"])
            async with async_session_factory() as db:
                job_id = await db.scalar(select(PlatformJob.id).where(
                    PlatformJob.job_type == job_type, PlatformJob.resource_type == "solution_export",
                    PlatformJob.resource_id == str(export_job_id),
                ))
            assert job_id is not None
        else:
            job_id = uuid.UUID(enqueued.json()["deploy_job_id" if solution_id is not None else "job_id"])

        async with async_session_factory() as db:
            job = await db.get(PlatformJob, job_id)
            assert job is not None
            assert job.execution_backend == "external_isolated"
            if backend == "kubernetes":
                job.execution_backend = "kubernetes"
            else:
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
                        supported_compute=(["app.compile.v2", "solution.archive.inspect.v1"]
                                           if job_type == "solution.deploy" else
                                           ["solution.archive.inspect.v1"] if job_type == "solution.export" else
                                           ["app.compile.v2"]),
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
        if backend == "kubernetes":
            # One additional job proves controller→manifest→normal launcher→API
            # compute. This fake cluster is local contract evidence, not Kind.
            from src.config import get_settings
            from src.jobs.schedulers.platform_jobs import claim_platform_job, IsolatedPods
            from src.jobs.schedulers.kubernetes_jobs import KubernetesBuildController
            from src.services.worker_pool.sizing import JobSizing
            from src.services.worker_pool.manifest import WorkerJobSpec
            from src.jobs.platform.kubernetes_compute import compute_worker_id

            claim = await claim_platform_job(backend="kubernetes", isolated=IsolatedPods(
                namespace="fixture", max_pods=1,
                sizing=JobSizing(launcher_memory_limit, launcher_memory_limit, .85, 64 * 2**20),
                standard_request_bytes=512 * 2**20,
            ), job_types=frozenset({job_type}))
            assert claim is not None and claim.id == job_id
            worker_id = compute_worker_id(claim.lease_token)
            cluster = AsyncMock()
            cluster.namespace = "fixture"
            cluster.get_job.return_value = None
            cluster.list_pods.return_value = []
            created_manifests: list[dict[str, Any]] = []

            async def create(manifest: dict[str, Any]) -> dict[str, Any]:
                rendered = {**manifest, "metadata": {**manifest["metadata"], "uid": "fixture-job-uid"}}
                created_manifests.append(rendered)
                return rendered

            cluster.create_job.side_effect = create
            monkeypatch.setenv("BIFROST_API_URL", e2e_api_url)
            controller = KubernetesBuildController(cluster, get_settings(), WorkerJobSpec(
                "fixture", "normal-worker-image", ({"secretRef": {"name": "must-not-inherit"}},), "default",
            ))
            await controller.reconcile_job(job_id)
            assert len(created_manifests) == 1
            rendered = created_manifests[0]
            cluster.get_job.return_value = rendered
            await controller.reconcile_job(job_id)
            cluster.start_job.assert_awaited_once()
            container = rendered["spec"]["template"]["spec"]["containers"][0]
            assert "envFrom" not in container
            environment = {
                "PATH": os.environ["PATH"], "PYTHONPATH": os.environ.get("PYTHONPATH", "/app"),
                "HOSTNAME": "fixture-compute-pod",
                **{item["name"]: item["value"] for item in container["env"] if "value" in item},
                "BIFROST_KUBERNETES_JOB_UID": "fixture-job-uid", "BIFROST_KUBERNETES_POD_UID": "fixture-pod-uid",
            }
            assert not {"BIFROST_SECRET_KEY", "BIFROST_DATABASE_URL", "BIFROST_REDIS_URL"} & environment.keys()
        process = subprocess.Popen(
            [sys.executable, "-m", "src.worker.main" if backend == "kubernetes" else "src.worker.remote.launcher"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        job = wait_for_job(e2e_client, headers, job_id, job_type, max_wait=120)
        assert job["attempt"] == 1

        if job_type == "solution.export":
            async with async_session_factory() as db:
                persisted = await db.get(PlatformJob, job_id)
                exported = await db.get(SolutionExportJob, export_job_id)
                assert persisted is not None and exported is not None
                assert persisted.executor_worker_id == worker_id and persisted.executor_attempt == 1
                assert persisted.executor_worker_form == ("job" if backend == "kubernetes" else "external")
                assert persisted.executor_compute_capability == "solution.archive.inspect.v1"
                assert exported.status == "completed" and exported.encrypted_options is None
                assert exported.artifact_storage_key.startswith(f"solution-exports/{solution_id}/{export_job_id}/")
                exported_digest = exported.artifact_sha256
            detail = e2e_client.get(f"/api/solutions/export-jobs/{export_job_id}", headers=headers)
            assert detail.status_code == 200, detail.text
            assert detail.json()["status"] == "completed" and detail.json()["download_url"]
            listing = e2e_client.get(f"/api/solutions/{solution_id}/export-jobs", headers=headers)
            assert listing.status_code == 200, listing.text
            assert any(item["id"] == str(export_job_id) and item["status"] == "completed" for item in listing.json()["jobs"])
            downloaded = e2e_client.get(f"/api/solutions/export-jobs/{export_job_id}/download", headers=headers)
            assert downloaded.status_code == 200, downloaded.text
            import hashlib
            assert hashlib.sha256(downloaded.content).hexdigest() == exported_digest
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                assert archive.read("modules/retained.py") == b"VALUE = 'stored source'\n"
                assert archive.read("README.md") == b"Current backup README"
                content = decode_secrets_blob(archive.read(".bifrost/secrets.enc").decode(), password=export_password)
                assert content.config_values == {exported_config_key: "API-only backup value"}
        else:
            async with async_session_factory() as db:
                persisted = await db.get(PlatformJob, job_id)
                app = await db.get(Application, app_id)
                assert persisted is not None and app is not None
                assert persisted.executor_worker_id == worker_id
                assert persisted.executor_attempt == 1
                assert persisted.executor_capacity_scope == scope
                assert persisted.executor_capacity_resource_uid == resource_uid
                assert persisted.executor_compute_capability == (
                    "solution.archive.inspect.v1" if solution_id is not None else "app.compile.v2"
                )
                assert app.active_deployment_id is not None
                deployment_id = app.active_deployment_id
            if solution_id is None:
                source = e2e_client.get(f"/api/applications/{app_id}/source", headers=headers)
                assert source.status_code == 200, source.text
                assert b"src/main.ts" in source.content
                assert await ApplicationSourceArtifactStorage().deployment_source_exists(app_id, deployment_id)
            else:
                retained = await SolutionSourceArtifactStorage(solution_id).read()
                assert retained is not None
                with zipfile.ZipFile(io.BytesIO(retained)) as archive:
                    assert f"apps/solution-app-{suffix}/src/main.ts" in archive.namelist()
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
            if export_job_id is not None:
                await db.execute(delete(SolutionExportJob).where(SolutionExportJob.id == export_job_id))
                await db.execute(delete(Config).where(Config.key == exported_config_key, Config.organization_id.is_(None)))
            if job_id is not None:
                await db.execute(delete(SolutionDeployJob).where(SolutionDeployJob.id == job_id))
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
        if solution_id is not None:
            deleted = e2e_client.delete(
                f"/api/solutions/{solution_id}", params={"confirm": solution_slug},
                headers=platform_admin.headers,
            )
            assert deleted.status_code == 200, deleted.text
        elif app_id is not None:
            deleted = e2e_client.delete(
                f"/api/applications/{app_id}", headers=platform_admin.headers
            )
            assert deleted.status_code == 204, deleted.text
        if fixture_configured:
            fixture = _capacity_fixture_request(
                "DELETE",
                f"/fixture/capacity/{scope}/{capacity_fixture_worker_id}",
                token=provider_token,
            )
            assert fixture.status_code == 204, fixture.text
