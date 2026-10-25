"""Live thin-worker coverage for build and Git-backed PlatformJob handlers.

This is one lifecycle because an independent App deploy creates the retained
source needed by the SDK update.  The remaining jobs each prove one canonical
handler crosses the thin worker boundary and leaves its durable outcome behind.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select, update

from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.services.github_config import delete_github_config, save_github_config
from tests.e2e.platform.thin_live_helpers import wait_for_job

pytestmark = pytest.mark.e2e


_SHARED_ROOT = Path("/tmp/bifrost/solution-repo-fixtures")


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _remove_fixture(path: Path) -> None:
    """Remove one test-owned shared fixture and fail loudly on cleanup errors."""
    shutil.rmtree(path)
    assert not path.exists()


def _delete_application(e2e_client, headers: dict[str, str], app_id: str) -> None:
    response = e2e_client.delete(f"/api/applications/{app_id}", headers=headers)
    assert response.status_code == 204, response.text


def _delete_solution(e2e_client, headers: dict[str, str], solution_id: str, slug: str) -> None:
    response = e2e_client.delete(
        f"/api/solutions/{solution_id}",
        params={"confirm": slug},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _vite_source_zip() -> bytes:
    """Return the smallest real Vite project that exercises source compilation."""
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "package.json",
            json.dumps(
                {
                    "name": "thin-live-app",
                    "private": True,
                    "devDependencies": {"vite": "^6.2.2"},
                }
            ),
        )
        archive.writestr(
            "index.html",
            "<!doctype html><html><body><div id=\"root\"></div>"
            "<script type=\"module\" src=\"/src/main.ts\"></script></body></html>",
        )
        archive.writestr(
            "src/main.ts",
            "document.querySelector('#root')!.textContent = 'thin worker build';\n",
        )
    return source.getvalue()


def _make_solution_repository(slug: str) -> Path:
    """Create a local Git Solution workspace visible to the thin worker."""
    root = _SHARED_ROOT / f"solution-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    (root / "bifrost.solution.yaml").write_text(
        f"slug: {slug}\nname: Thin Git Solution\nversion: 1.0.0\n"
    )
    (root / "README.md").write_text("# Thin Git Solution\n")
    workflows = root / "workflows"
    workflows.mkdir()
    (workflows / "thin_git.py").write_text(
        "from bifrost import workflow\n\n"
        "@workflow(name='Thin Git Workflow')\n"
        "async def thin_git_workflow():\n"
        "    return 'synced'\n"
    )
    _run_git("init", "-q", "-b", "main", cwd=root)
    _run_git("add", "-A", cwd=root)
    _run_git("-c", "user.email=thin@example.test", "-c", "user.name=Thin", "commit", "-qm", "init", cwd=root)
    return root


def _make_workspace_remote() -> tuple[str, Path]:
    """Create the local bare remote reached through the thin-live URL rewrite."""
    owner = f"thin-{uuid.uuid4().hex[:8]}"
    repository = "workspace"
    remote = _SHARED_ROOT / "thin-github" / owner / f"{repository}.git"
    remote.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    (remote / "HEAD").write_text("ref: refs/heads/main\n")

    seed = remote.parent / "seed"
    seed.mkdir()
    (seed / "thin-live-workspace.txt").write_text("fetched through the thin worker\n")
    _run_git("init", "-q", "-b", "main", cwd=seed)
    _run_git("add", "-A", cwd=seed)
    _run_git("-c", "user.email=thin@example.test", "-c", "user.name=Thin", "commit", "-qm", "init", cwd=seed)
    _run_git("remote", "add", "origin", f"file://{remote}", cwd=seed)
    _run_git("push", "-qu", "origin", "main", cwd=seed)
    return f"{owner}/{repository}", remote.parent


async def _platform_job_id(async_session_factory, *, job_type: str, resource_id: str) -> uuid.UUID:
    async with async_session_factory() as db:
        job_id = await db.scalar(
            select(PlatformJob.id).where(
                PlatformJob.job_type == job_type,
                PlatformJob.resource_id == resource_id,
            )
        )
    assert job_id is not None, f"missing {job_type} PlatformJob for {resource_id}"
    return job_id


async def test_thin_worker_executes_build_and_git_platform_jobs(
    e2e_client, platform_admin, async_session_factory, request
) -> None:
    """Run the seven build/Git handlers through physical thin worker children.

    Cost: seven PlatformJobs.  The deploy and SDK-update jobs each perform a
    real Vite build because their transition proves retained-source rebuilding;
    publish proves the legacy bundle promotion path; deploy/export prove the
    Solution artifact paths; and the two Git jobs use local repositories on the
    shared test mount, so no external service is involved.
    """
    headers = platform_admin.headers
    suffix = uuid.uuid4().hex[:8]

    app_response = e2e_client.post(
        "/api/applications",
        headers=headers,
        json={
            "name": f"Thin Build {suffix}",
            "slug": f"thin-build-{suffix}",
            "app_model": "standalone_v2",
        },
    )
    assert app_response.status_code == 201, app_response.text
    app = app_response.json()
    request.addfinalizer(
        lambda: _delete_application(e2e_client, headers, app["id"])
    )
    upload_headers = {
        key: value for key, value in headers.items() if key.lower() != "content-type"
    }
    deploy_response = e2e_client.post(
        f"/api/applications/{app['id']}/deploy",
        headers=upload_headers,
        files={"source": ("source.zip", _vite_source_zip(), "application/zip")},
    )
    assert deploy_response.status_code == 202, deploy_response.text
    app_deploy = wait_for_job(
        e2e_client,
        headers,
        deploy_response.json()["job_id"],
        "application.deploy",
    )
    first_deployment_id = app_deploy["result"]["deployment_id"]
    source = e2e_client.get(f"/api/applications/{app['id']}/source", headers=headers)
    assert source.status_code == 200, source.text
    with zipfile.ZipFile(io.BytesIO(source.content)) as archive:
        assert archive.read("src/main.ts") == b"document.querySelector('#root')!.textContent = 'thin worker build';\n"

    # The deploy stamped current SDK provenance. Mark this otherwise-valid
    # retained source stale so the public update endpoint enqueues its normal
    # optimistic-concurrency rebuild rather than refusing an up-to-date App.
    async with async_session_factory() as db:
        await db.execute(
            update(Application)
            .where(Application.id == uuid.UUID(app["id"]))
            .values(sdk_fingerprint="thin-live-stale-sdk")
        )
        await db.commit()
    sdk_update_response = e2e_client.post(
        f"/api/applications/{app['id']}/sdk/update", headers=headers
    )
    assert sdk_update_response.status_code == 202, sdk_update_response.text
    sdk_update = wait_for_job(
        e2e_client,
        headers,
        sdk_update_response.json()["job_id"],
        "application.sdk_update",
    )
    assert sdk_update["result"]["deployment_id"] != first_deployment_id
    updated_app = e2e_client.get(f"/api/applications/{app['slug']}", headers=headers)
    assert updated_app.status_code == 200, updated_app.text
    assert updated_app.json()["sdk_fingerprint"]
    assert updated_app.json()["sdk_fingerprint"] != "thin-live-stale-sdk"

    publish_response = e2e_client.post(
        "/api/applications",
        headers=headers,
        json={
            "name": f"Thin Publish {suffix}",
            "slug": f"thin-publish-{suffix}",
            "app_model": "inline_v1",
        },
    )
    assert publish_response.status_code == 201, publish_response.text
    publish_app = publish_response.json()
    request.addfinalizer(
        lambda: _delete_application(e2e_client, headers, publish_app["id"])
    )
    publish_job_response = e2e_client.post(
        f"/api/applications/{publish_app['id']}/publish", headers=headers
    )
    assert publish_job_response.status_code == 202, publish_job_response.text
    published = wait_for_job(
        e2e_client,
        headers,
        publish_job_response.json()["job_id"],
        "application.publish",
    )
    assert published["result"]["files_published"] >= 2

    solution_response = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": f"thin-solution-{suffix}",
            "name": "Thin Solution",
            "organization_id": None,
        },
    )
    assert solution_response.status_code == 201, solution_response.text
    solution_id = solution_response.json()["id"]
    request.addfinalizer(
        lambda: _delete_solution(
            e2e_client,
            headers,
            solution_id,
            f"thin-solution-{suffix}",
        )
    )
    workflow_name = f"Thin Solution Workflow {suffix}"
    workflow_source = (
        "from bifrost import workflow\n\n"
        f"@workflow(name={workflow_name!r})\n"
        "async def thin_solution_workflow():\n"
        "    return 'deployed'\n"
    )
    solution_deploy_response = e2e_client.post(
        f"/api/solutions/{solution_id}/deploy",
        headers=headers,
        json={
            "python_files": {"workflows/thin_solution.py": workflow_source},
            "workflows": [
                {
                    "id": str(uuid.uuid4()),
                    "name": workflow_name,
                    "function_name": "thin_solution_workflow",
                    "path": "workflows/thin_solution.py",
                    "type": "workflow",
                    "source": workflow_source,
                }
            ],
        },
    )
    assert solution_deploy_response.status_code == 202, solution_deploy_response.text
    solution_deploy = wait_for_job(
        e2e_client,
        headers,
        solution_deploy_response.json()["deploy_job_id"],
        "solution.deploy",
    )
    assert solution_deploy["result"]["workflows_upserted"] == 1

    export_response = e2e_client.post(
        f"/api/solutions/{solution_id}/export-jobs",
        headers=headers,
        json={
            "options": {
                "include_configs": True,
                "include_secrets": False,
                "include_tables": False,
                "include_files": False,
                "password": "thin-worker-export-password",
            }
        },
    )
    assert export_response.status_code == 202, export_response.text
    export_id = export_response.json()["id"]
    export_job_id = await _platform_job_id(
        async_session_factory,
        job_type="solution.export",
        resource_id=export_id,
    )
    wait_for_job(e2e_client, headers, export_job_id, "solution.export")
    exported = e2e_client.get(f"/api/solutions/export-jobs/{export_id}", headers=headers)
    assert exported.status_code == 200, exported.text
    assert exported.json()["status"] == "completed"
    download = e2e_client.get(
        f"/api/solutions/export-jobs/{export_id}/download", headers=headers
    )
    assert download.status_code == 200, download.text
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert ".bifrost/workflows.yaml" in archive.namelist()

    git_solution_slug = f"thin-git-solution-{suffix}"
    git_solution_repo = _make_solution_repository(git_solution_slug)
    git_solution = Solution(
        id=uuid.uuid4(),
        slug=git_solution_slug,
        name="Thin Git Solution",
        git_connected=True,
        git_repo_url=f"file://{git_solution_repo}",
        git_ref="main",
    )
    async with async_session_factory() as db:
        db.add(git_solution)
        await db.commit()
    request.addfinalizer(
        lambda: _delete_solution(
            e2e_client,
            headers,
            str(git_solution.id),
            git_solution_slug,
        )
    )
    try:
        sync_response = e2e_client.post(
            f"/api/solutions/{git_solution.id}/sync", headers=headers
        )
        assert sync_response.status_code == 202, sync_response.text
        solution_sync = wait_for_job(
            e2e_client,
            headers,
            sync_response.json()["job_id"],
            "solution.git_sync",
        )
        assert solution_sync["result"] == {
            "solution_id": str(git_solution.id),
            "status": "synced",
        }
        synced_solution = e2e_client.get(
            f"/api/solutions/{git_solution.id}", headers=headers
        )
        assert synced_solution.status_code == 200, synced_solution.text
        assert synced_solution.json()["version"] == "1.0.0"
    finally:
        _remove_fixture(git_solution_repo)

    workspace_repo, workspace_root = _make_workspace_remote()
    try:
        async with async_session_factory() as db:
            await save_github_config(
                db,
                org_id=None,
                token="thin-worker-local-git-token",
                repo_url=workspace_repo,
                branch="main",
                updated_by="thin-live-e2e",
            )
        fetch_response = e2e_client.post("/api/github/fetch", headers=headers)
        assert fetch_response.status_code == 202, fetch_response.text
        workspace_fetch = wait_for_job(
            e2e_client,
            headers,
            fetch_response.json()["job_id"],
            "workspace.git",
        )
        assert workspace_fetch["result"]["success"] is True
        assert workspace_fetch["result"]["remote_branch_exists"] is True
        assert workspace_fetch["result"]["commits_behind"] == 0
    finally:
        async with async_session_factory() as db:
            await delete_github_config(db, None)
        _remove_fixture(workspace_root)
