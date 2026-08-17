"""Real queue/Worker proof for the PlatformJob-backed Solution build plane."""

from __future__ import annotations

import uuid

import pytest

from src.models.contracts.sandbox_runner import SandboxRunnerConfigSave
from src.models.orm.platform_jobs import PlatformJob
from src.services.builder.build_requests import await_build_jobs, request_app_build
from src.services.builder.staged_artifacts import StagedBuildArtifactStorage
from src.services.sandbox_runner_config import SandboxRunnerConfigService
from src.services.solutions.app_build import SolutionAppBuilder

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_platform_job_dispatches_real_local_worker_and_finalizes_dist(
    e2e_client,
    platform_admin,
    async_session_factory,
) -> None:
    """Scheduler claim -> existing Worker -> npm/Vite -> staged S3 -> app dist."""
    from src.jobs.rabbitmq import rabbitmq

    # The test process owns its publisher pool while the scheduler and Worker
    # use separate process-local pools.  A preceding async E2E may have left
    # this one attached to a closed event loop.
    rabbitmq.reset_pools()

    async with async_session_factory() as db:
        service = SandboxRunnerConfigService(db)
        await service.save_config(
            SandboxRunnerConfigSave(provider="local", enabled=True),
            callback_base_url=None,
            updated_by=platform_admin.email,
        )
        await service.set_runtime_status(
            provisioned=True,
            connected=True,
            updated_by=platform_admin.email,
        )
        await db.commit()

    slug = f"worker-build-{uuid.uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/solutions",
        headers=platform_admin.headers,
        json={
            "slug": slug,
            "name": "Worker build E2E",
            "organization_id": None,
            "global_repo_access": False,
        },
    )
    assert created.status_code in (200, 201), created.text
    solution_id = uuid.UUID(created.json()["id"])
    app_id = uuid.uuid4()
    source_files = {
        "src/main.tsx": b"""
export function mount(element: HTMLElement) {
  element.textContent = "platform-job-worker-build-ok";
  return () => { element.textContent = ""; };
}
""",
    }

    try:
        queued = await request_app_build(
            solution_id=solution_id,
            app_id=app_id,
            requested_by=platform_admin.user_id,
            src_files=source_files,
            dependencies={},
        )

        completed = (await await_build_jobs([queued], timeout_s=180))[0]
        assert completed.status == "succeeded"
        assert completed.output_manifest
        paths = {entry["path"] for entry in completed.output_manifest}
        assert "index.html" in paths
        assert any(path.startswith("assets/") for path in paths)

        staged = StagedBuildArtifactStorage(completed.id)
        await staged.verify_manifest(app_id, completed.output_manifest)
        await staged.copy_outputs_to_app_dist(app_id, completed.output_manifest)
        built_index = await SolutionAppBuilder().read_dist(app_id, "index.html")
        assert b"bifrost-app-runtime" in built_index

        reused = await request_app_build(
            solution_id=solution_id,
            app_id=app_id,
            requested_by=platform_admin.user_id,
            src_files=source_files,
            dependencies={},
        )
        assert reused.id == completed.id

        async with async_session_factory() as db:
            central = await db.get(PlatformJob, completed.id)
            assert central is not None
            assert central.job_type == "solution.build"
            assert central.status == "succeeded"
            assert central.external_provider == "local"
            assert central.result["build_job_id"] == str(completed.id)
    finally:
        await SolutionAppBuilder().delete_dist(app_id)
        async with async_session_factory() as db:
            await SandboxRunnerConfigService(db).delete_config()
            await db.commit()
        e2e_client.delete(
            f"/api/solutions/{solution_id}",
            headers=platform_admin.headers,
            params={"confirm": slug},
        )
