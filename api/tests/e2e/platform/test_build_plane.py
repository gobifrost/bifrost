"""End-to-end proof for the dedicated, credential-separated app build plane."""

from __future__ import annotations

import io
import uuid
import zipfile
from contextlib import asynccontextmanager

import pytest

from src.services.builder.build_requests import await_build_jobs, request_app_build
from src.models.orm.platform_jobs import PlatformJob
from src.services.builder.staged_artifacts import StagedBuildArtifactStorage
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _source_app_zip(slug: str, app_id: uuid.UUID, app_slug: str) -> bytes:
    files = {
        "bifrost.solution.yaml": (
            f"slug: {slug}\n"
            "name: Build Plane Deploy E2E\n"
        ),
        ".bifrost/apps.yaml": (
            "apps:\n"
            f"  {app_id}:\n"
            f"    id: {app_id}\n"
            "    path: apps/web\n"
            f"    slug: {app_slug}\n"
            "    name: Built by runner\n"
            "    app_model: standalone_v2\n"
            "    access_level: authenticated\n"
            "    dependencies: {}\n"
        ),
        "apps/web/src/main.tsx": """
import React from "react";
import { createRoot } from "react-dom/client";

export function mount(element: HTMLElement) {
  const root = createRoot(element);
  root.render(React.createElement("main", null, "deployed-build-plane-ok"));
  return () => root.unmount();
}
""",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


@pytest.mark.asyncio
async def test_real_coordinator_runner_and_staged_artifacts(
    e2e_client,
    platform_admin,
    async_session_factory,
    monkeypatch,
) -> None:
    """RabbitMQ claim -> runner -> per-file S3 staging succeeds without mocks."""
    from src.jobs.rabbitmq import rabbitmq

    # The in-process publisher pool is module-global while pytest gives each
    # async test a fresh loop. A preceding E2E test may therefore leave a pool
    # pinned to a loop that has already closed. The worker/coordinator use
    # their own process-local pools; reset only this test process's publisher
    # before exercising the real queue.
    rabbitmq.reset_pools()

    @asynccontextmanager
    async def test_db_context():
        async with async_session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(
        "src.services.builder.build_requests.get_db_context",
        test_db_context,
    )

    slug = f"build-plane-{uuid.uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/solutions",
        headers=platform_admin.headers,
        json={
            "slug": slug,
            "name": "Build Plane E2E",
            "organization_id": None,
            "global_repo_access": False,
        },
    )
    assert created.status_code in (200, 201), created.text
    solution_id = uuid.UUID(created.json()["id"])
    app_id = uuid.uuid4()

    try:
        queued = await request_app_build(
            solution_id=solution_id,
            app_id=app_id,
            requested_by=platform_admin.user_id,
            src_files={
                "src/main.tsx": b"""
import React from "react";
import { createRoot } from "react-dom/client";

export function mount(element: HTMLElement) {
  const root = createRoot(element);
  root.render(React.createElement("main", null, "build-plane-ok"));
  return () => root.unmount();
}
""",
            },
            dependencies={},
        )

        completed = (await await_build_jobs([queued], timeout_s=120))[0]
        assert completed.status == "succeeded"
        assert completed.output_manifest
        assert {entry["path"] for entry in completed.output_manifest} >= {
            "index.html",
        }
        assert any(
            entry["path"].startswith("assets/")
            for entry in completed.output_manifest
        )
        await StagedBuildArtifactStorage(completed.id).verify_manifest(
            app_id,
            completed.output_manifest,
        )
        async with async_session_factory() as db:
            central = await db.get(PlatformJob, completed.id)
            assert central is not None
            assert central.job_type == "solution.build"
            assert central.status == "succeeded"
            assert central.result["build_job_id"] == str(completed.id)
    finally:
        e2e_client.delete(
            f"/api/solutions/{solution_id}",
            headers=platform_admin.headers,
            params={"confirm": slug},
        )


def test_source_app_deploy_builds_and_finalizes_real_dist(
    e2e_client,
    platform_admin,
) -> None:
    """A source-only Solution app becomes a deployed, fetchable dist."""
    headers = platform_admin.headers
    upload_headers = {
        key: value for key, value in headers.items() if key.lower() != "content-type"
    }
    slug = f"build-deploy-{uuid.uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": slug,
            "name": "Build Plane Deploy E2E",
            "organization_id": None,
            "global_repo_access": False,
        },
    )
    assert created.status_code in (200, 201), created.text
    solution_id = uuid.UUID(created.json()["id"])
    manifest_app_id = uuid.uuid4()
    deployed_app_id = solution_entity_id(solution_id, manifest_app_id)
    app_slug = f"runner-{uuid.uuid4().hex[:8]}"

    try:
        response = e2e_client.post(
            f"/api/solutions/{solution_id}/deploy",
            headers=upload_headers,
            files={
                "file": (
                    f"{slug}.zip",
                    _source_app_zip(slug, manifest_app_id, app_slug),
                    "application/zip",
                )
            },
        )
        deployed = wait_for_deploy(
            e2e_client,
            response,
            headers,
            timeout_s=120,
        )
        assert deployed.status_code == 200, deployed.text
        assert deployed.json()["apps_upserted"] == 1

        app = e2e_client.get(f"/api/applications/{app_slug}", headers=headers)
        assert app.status_code == 200, app.text
        assert app.json()["id"] == str(deployed_app_id)

        index = e2e_client.get(
            f"/api/applications/{deployed_app_id}/dist/index.html",
            headers=headers,
        )
        assert index.status_code == 200, index.text
        assert "bifrost-app-runtime" in index.text
        manifest = e2e_client.get(
            f"/api/applications/{deployed_app_id}/bundle-manifest?mode=live",
            headers=headers,
        )
        assert manifest.status_code == 200, manifest.text
        assert manifest.json()["runtime_contract"] == "mount-v1"
    finally:
        e2e_client.delete(
            f"/api/solutions/{solution_id}",
            headers=headers,
            params={"confirm": slug},
        )
