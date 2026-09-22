"""Repository snapshot preview/import for workspace bundles (live API boundary).

The clone runs server-side in the API container, so fixture repos are staged
under /tmp/bifrost — bind-mounted into both test-runner and API container.
"""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_SHARED_ROOT = Path("/tmp/bifrost/solution-repo-fixtures")
_CREATED: list[Path] = []


def _write_tree(sol: Path, suffix: str) -> tuple[str, str]:
    workflow_id = str(uuid.uuid4())
    path = f"workflows/repo_{suffix}.py"
    function_name = f"repo_fn_{suffix}"
    source = f"def {function_name}():\n    return 1\n"
    (sol / "bifrost.solution.yaml").write_text(
        f"slug: repo-snap-{suffix}\nname: Repo Snap {suffix}\nversion: 1.0.0\n"
    )
    (sol / "workflows").mkdir(parents=True, exist_ok=True)
    (sol / path).write_text(source)
    bifrost = sol / ".bifrost"
    bifrost.mkdir(exist_ok=True)
    (bifrost / "workflows.yaml").write_text(
        f"workflows:\n  {workflow_id}:\n    path: {path}\n"
        f"    function_name: {function_name}\n    name: {function_name}\n"
    )
    return path, function_name


def _make_repo(suffix: str, *, subdir: str = "") -> tuple[str, str]:
    _SHARED_ROOT.mkdir(parents=True, exist_ok=True)
    root = _SHARED_ROOT / f"ws-repo-{suffix}"
    _CREATED.append(root)
    sol = root / subdir if subdir else root
    sol.mkdir(parents=True)
    _write_tree(sol, suffix)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return f"file://{root}", out.stdout.strip()


def _zip_of(root: Path) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            zf.writestr(path.relative_to(root).as_posix(), path.read_bytes())
    return buf.getvalue()


def _plan_key(preview: dict) -> dict[str, tuple[str, str]]:
    return {
        item["id"].split(":", 1)[1] if ":" in item["id"] else item["id"]: (
            item["classification"],
            item.get("match_key") or "",
        )
        for item in preview["items"]
    }


@pytest.fixture(autouse=True)
def _cleanup_shared():
    yield
    while _CREATED:
        shutil.rmtree(_CREATED.pop(), ignore_errors=True)


async def test_repo_and_zip_previews_converge(e2e_client, platform_admin) -> None:
    suffix = uuid.uuid4().hex[:8]
    repo_url, commit = _make_repo(suffix)
    root = Path(repo_url.removeprefix("file://"))

    zip_preview = e2e_client.post(
        "/api/solutions/import-workspace/preview",
        headers={k: v for k, v in platform_admin.headers.items() if k.lower() != "content-type"},
        files={"file": ("workspace.zip", _zip_of(root), "application/zip")},
    )
    assert zip_preview.status_code == 200, zip_preview.text

    repo_preview = e2e_client.post(
        "/api/solutions/import-workspace/preview-repo",
        headers=platform_admin.headers,
        json={"repo_url": repo_url},
    )
    assert repo_preview.status_code == 200, repo_preview.text
    body = repo_preview.json()
    assert body["source_kind"] == "repo"
    assert body["repo_url"] == repo_url
    assert body["resolved_commit"] == commit

    assert _plan_key(body) == _plan_key(zip_preview.json())

    from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage

    metadata = await WorkspaceBundleStorage(body["preview_token"]).load_metadata()
    assert metadata["repo_url"] == repo_url
    assert metadata["resolved_commit"] == commit
    assert metadata["package_sha256"] == body["package_sha256"]
    await WorkspaceBundleStorage(body["preview_token"]).delete()
    await WorkspaceBundleStorage(zip_preview.json()["preview_token"]).delete()


async def test_repo_preview_rejects_bad_ref_subpath_and_missing_descriptor(
    e2e_client, platform_admin
) -> None:
    suffix = uuid.uuid4().hex[:8]
    repo_url, _ = _make_repo(suffix)

    bad_ref = e2e_client.post(
        "/api/solutions/import-workspace/preview-repo",
        headers=platform_admin.headers,
        json={"repo_url": repo_url, "git_ref": "no-such-ref"},
    )
    assert bad_ref.status_code == 422, bad_ref.text

    bad_path = e2e_client.post(
        "/api/solutions/import-workspace/preview-repo",
        headers=platform_admin.headers,
        json={"repo_url": repo_url, "repo_subpath": "../escape"},
    )
    assert bad_path.status_code == 422, bad_path.text

    _SHARED_ROOT.mkdir(parents=True, exist_ok=True)
    empty = _SHARED_ROOT / f"ws-empty-{suffix}"
    _CREATED.append(empty)
    empty.mkdir(parents=True)
    (empty / "note.txt").write_text("hi")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=empty, check=True)
    subprocess.run(["git", "add", "-A"], cwd=empty, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=empty,
        check=True,
    )
    missing = e2e_client.post(
        "/api/solutions/import-workspace/preview-repo",
        headers=platform_admin.headers,
        json={"repo_url": f"file://{empty}"},
    )
    assert missing.status_code == 422, missing.text
    assert "bifrost.solution.yaml" in missing.text


async def test_repo_preview_rejects_symlinked_workspaces(e2e_client, platform_admin) -> None:
    suffix = uuid.uuid4().hex[:8]
    repo_url, _ = _make_repo(suffix)
    root = Path(repo_url.removeprefix("file://"))
    (root / "workflows" / "linked.py").symlink_to(root / "workflows" / f"repo_{suffix}.py")
    import os

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.t",
    }
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "link"], cwd=root, check=True, capture_output=True, env=env
    )
    refused = e2e_client.post(
        "/api/solutions/import-workspace/preview-repo",
        headers=platform_admin.headers,
        json={"repo_url": repo_url},
    )
    assert refused.status_code == 422, refused.text
    assert "symlink" in refused.text.lower()


async def test_repo_snapshot_import_creates_unattached_content(
    e2e_client, platform_admin, db_session
) -> None:
    suffix = uuid.uuid4().hex[:8]
    repo_url, _ = _make_repo(suffix)
    preview = e2e_client.post(
        "/api/solutions/import-workspace/preview-repo",
        headers=platform_admin.headers,
        json={"repo_url": repo_url},
    ).json()
    accepted = e2e_client.post(
        "/api/solutions/import-workspace",
        headers=platform_admin.headers,
        json={"preview_token": preview["preview_token"], "decisions": []},
    )
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]

    job: dict = {}
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=platform_admin.headers)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.25)
    assert job["status"] == "succeeded", job

    from sqlalchemy import select

    from src.models.orm.solutions import Solution
    from src.models.orm.workflows import Workflow

    rows = (await db_session.execute(select(Solution))).scalars().all()
    assert [s.slug for s in rows if s.slug == f"repo-snap-{suffix}"] == []
    imported = (
        await db_session.execute(
            select(Workflow).where(Workflow.function_name == f"repo_fn_{suffix}")
        )
    ).scalars().all()
    assert len(imported) == 1
    assert imported[0].solution_id is None
    assert imported[0].organization_id is None

    # Dirty state is asserted server-side: an in-process redis read would reuse
    # a connection cached on an earlier test's (now closed) event loop when
    # several e2e files share one runner process.
    status = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers)
    assert status.status_code == 200, status.text
    assert status.json()["dirty"] is True
