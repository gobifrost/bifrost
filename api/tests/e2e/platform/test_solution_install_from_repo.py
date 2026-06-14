"""E2E: preview a Solution install plan sourced from a git repo (Task 4).

``POST /api/solutions/install/preview-repo`` clones a repo (optionally at a
subfolder/ref), parses the workspace, and returns the SAME
``SolutionInstallPreview`` the zip preview returns — parse-only, no DB write.

The clone runs server-side in the API container, so the fixture repo is staged
under ``/tmp/bifrost`` — the per-worktree host dir bind-mounted into BOTH the
test-runner and the API container. ``file://`` clones work offline; the git
binary is present in both containers.
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

# Bind-mounted into both the test-runner and the API container, so a file://
# clone the API performs can read a repo the test-runner just wrote.
_SHARED_ROOT = Path("/tmp/bifrost/solution-repo-fixtures")

# Per-test fixture repos to rmtree on teardown (only what this test created, so
# the cleanup is safe under future parallel runs).
_CREATED: list[Path] = []


def _make_fixture_repo(subdir: str = "", *, with_connection: bool = False) -> str:
    """Create a git repo with a minimal solution workspace (optionally in a
    subfolder) on the shared mount and return a file:// clone URL.

    ``with_connection`` writes a ``.bifrost/connections.yaml`` declaring one
    connection prerequisite, so the preview's ``connection_schemas`` is non-empty.
    """
    _SHARED_ROOT.mkdir(parents=True, exist_ok=True)
    root = _SHARED_ROOT / f"repo-{uuid.uuid4().hex[:8]}"
    _CREATED.append(root)
    sol = root / subdir if subdir else root
    sol.mkdir(parents=True)
    (sol / "bifrost.solution.yaml").write_text(
        "slug: fixture-sol\n"
        "name: Fixture Solution\n"
        "version: 1.0.0\n"
        "scope: org\n"
    )
    if with_connection:
        bifrost_dir = sol / ".bifrost"
        bifrost_dir.mkdir()
        (bifrost_dir / "connections.yaml").write_text(
            "connections:\n"
            "  microsoft:\n"
            "    integration_name: microsoft\n"
            "    template: {}\n"
            "    position: 0\n"
        )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    return f"file://{root}"


@pytest.fixture(autouse=True)
def _cleanup_shared_fixtures():
    yield
    while _CREATED:
        shutil.rmtree(_CREATED.pop(), ignore_errors=True)


async def test_preview_repo_resolves_descriptor_at_subpath(e2e_client, platform_admin):
    repo_url = _make_fixture_repo(subdir="microsoft-csp", with_connection=True)
    resp = e2e_client.post(
        "/api/solutions/install/preview-repo",
        json={"repo_url": repo_url, "repo_subpath": "microsoft-csp"},
        headers=platform_admin.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slug"] == "fixture-sol"
    assert body["version"] == "1.0.0"
    # Regression: the preview must surface declared connection prerequisites
    # (previously dropped — defeated the connection-refs feature at confirmation).
    assert body["connection_schemas"], body
    assert body["connection_schemas"][0]["integration_name"] == "microsoft"


async def test_preview_repo_root_descriptor(e2e_client, platform_admin):
    repo_url = _make_fixture_repo()  # descriptor at repo root
    resp = e2e_client.post(
        "/api/solutions/install/preview-repo",
        json={"repo_url": repo_url},
        headers=platform_admin.headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["slug"] == "fixture-sol"


async def test_preview_repo_rejects_traversing_subpath(e2e_client, platform_admin):
    repo_url = _make_fixture_repo()
    resp = e2e_client.post(
        "/api/solutions/install/preview-repo",
        json={"repo_url": repo_url, "repo_subpath": "../escape"},
        headers=platform_admin.headers,
    )
    assert resp.status_code == 422, resp.text
    assert "escapes the repo checkout" in resp.text
