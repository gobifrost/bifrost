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
import time
import uuid
from pathlib import Path

import pytest

from tests.e2e.platform.conftest import wait_for_install

pytestmark = pytest.mark.e2e

# Bind-mounted into both the test-runner and the API container, so a file://
# clone the API performs can read a repo the test-runner just wrote.
_SHARED_ROOT = Path("/tmp/bifrost/solution-repo-fixtures")

# Per-test fixture repos to rmtree on teardown (only what this test created, so
# the cleanup is safe under future parallel runs).
_CREATED: list[Path] = []


def _make_fixture_repo(
    subdir: str = "",
    *,
    with_connection: bool = False,
    slug: str = "fixture-sol",
    broken_manifest: bool = False,
) -> str:
    """Create a git repo with a minimal solution workspace (optionally in a
    subfolder) on the shared mount and return a file:// clone URL.

    ``with_connection`` writes a ``.bifrost/connections.yaml`` declaring one
    connection prerequisite, so the preview's ``connection_schemas`` is non-empty.
    ``slug`` overrides the descriptor slug (e2e DB state is session-scoped and
    NOT reset between tests, so install tests that must start clean pass a unique
    slug to avoid colliding with installs a sibling test already created).
    ``broken_manifest`` writes a ``.bifrost/tables.yaml`` whose YAML is VALID (so
    clone + descriptor parse + flush all succeed) but whose table ``policies`` AST
    is malformed, so ``deploy_from_workspace`` raises during the DB phase — this
    is what exercises the rollback branch (a broken-YAML manifest would instead
    fail earlier, inside the parse step, before any row is created).
    """
    _SHARED_ROOT.mkdir(parents=True, exist_ok=True)
    root = _SHARED_ROOT / f"repo-{uuid.uuid4().hex[:8]}"
    _CREATED.append(root)
    sol = root / subdir if subdir else root
    sol.mkdir(parents=True)
    (sol / "bifrost.solution.yaml").write_text(
        f"slug: {slug}\n"
        "name: Fixture Solution\n"
        "version: 1.0.0\n"
        "scope: org\n"
    )
    if broken_manifest:
        bifrost_dir = sol / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        # Valid YAML, but a table whose `policies` is a string (not a list[Policy]).
        # _parse_workspace collects tables WITHOUT validating policies, so clone +
        # descriptor parse + flush all succeed; deploy validates the policy AST and
        # raises, so the rollback branch must undo the just-created install row.
        (bifrost_dir / "tables.yaml").write_text(
            "tables:\n"
            "  11111111-1111-1111-1111-111111111111:\n"
            "    id: 11111111-1111-1111-1111-111111111111\n"
            "    name: broken_table\n"
            "    columns: []\n"
            "    policies: not-a-valid-policy-list\n"
        )
    if with_connection:
        bifrost_dir = sol / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
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


async def test_connected_install_lifecycle(e2e_client, platform_admin, db_session):
    """One repo install covers sync, connection export, writer guards, and deletion."""
    from src.models.orm.solutions import Solution as SolutionORM

    slug = f"fromrepo-{uuid.uuid4().hex[:8]}"
    repo_url = _make_fixture_repo(
        subdir="microsoft-csp", slug=slug, with_connection=True
    )
    resp = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install/from-repo",
            json={"repo_url": repo_url, "repo_subpath": "microsoft-csp"},
            headers=platform_admin.headers,
        ),
        platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    sol = resp.json()
    assert sol["git_connected"] is True
    assert sol["repo_subpath"] == "microsoft-csp"
    assert sol["slug"] == slug
    # deploy is now refused — auto-pull is the only writer
    dep = e2e_client.post(
        f"/api/solutions/{sol['id']}/deploy", json={}, headers=platform_admin.headers
    )
    assert dep.status_code == 409, dep.text

    # The slug+scope conflict is a synchronous fast-path 409 (the install row was
    # created before the first job even ran), so the second POST refuses directly.
    again = e2e_client.post(
        "/api/solutions/install/from-repo",
        json={"repo_url": repo_url, "repo_subpath": "microsoft-csp"},
        headers=platform_admin.headers,
    )
    assert again.status_code == 409, again.text

    # Auto-pull through the durable job clears the scheduler's update signal.
    row = await db_session.get(SolutionORM, uuid.UUID(sol["id"]))
    assert row is not None
    row.update_available_version = "1.1.0"
    await db_session.commit()
    before = e2e_client.get(
        f"/api/solutions/{sol['id']}", headers=platform_admin.headers
    )
    assert before.status_code == 200, before.text
    assert before.json()["update_available_version"] == "1.1.0"

    synced = e2e_client.post(
        f"/api/solutions/{sol['id']}/sync", headers=platform_admin.headers
    )
    assert synced.status_code == 202, synced.text
    job_id = synced.json()["job_id"]
    assert synced.headers["Location"] == f"/api/platform-jobs/{job_id}"
    job = {}
    for _ in range(240):
        status_response = e2e_client.get(
            f"/api/platform-jobs/{job_id}", headers=platform_admin.headers
        )
        assert status_response.status_code == 200, status_response.text
        job = status_response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.25)
    assert job["status"] == "succeeded", job
    after = e2e_client.get(
        f"/api/solutions/{sol['id']}", headers=platform_admin.headers
    )
    assert after.status_code == 200, after.text
    assert after.json()["update_available_version"] is None

    # Export rebuilds from the installed connection declaration. Previewing the
    # exported ZIP proves the declaration survives distribution.
    exp = e2e_client.post(
        f"/api/solutions/{sol['id']}/export?mode=shareable",
        headers=platform_admin.headers,
    )
    assert exp.status_code == 200, exp.text
    upload_headers = {
        k: v for k, v in platform_admin.headers.items() if k.lower() != "content-type"
    }
    prev = e2e_client.post(
        "/api/solutions/install/preview",
        files={"file": ("backup.zip", exp.content, "application/zip")},
        headers=upload_headers,
    )
    assert prev.status_code == 200, prev.text
    names = [
        c["integration_name"]
        for c in (prev.json().get("connection_schemas") or [])
    ]
    assert "microsoft" in names, prev.json()

    # A connected install with a connection child must delete without the
    # read-only guard rejecting the child's DB cascade.
    deleted = e2e_client.delete(
        f"/api/solutions/{sol['id']}?confirm={slug}",
        headers=platform_admin.headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert e2e_client.get(
        f"/api/solutions/{sol['id']}", headers=platform_admin.headers
    ).status_code == 404


async def test_install_from_repo_rolls_back_on_deploy_failure(e2e_client, platform_admin):
    slug = f"fromrepo-{uuid.uuid4().hex[:8]}"
    # First repo: descriptor is valid (clone/parse/flush succeed) but a malformed
    # .bifrost/forms.yaml makes the bundle read inside deploy_from_workspace raise.
    bad = _make_fixture_repo(slug=slug, broken_manifest=True)
    # Clone + create succeed synchronously (202); the deploy fails INSIDE the job,
    # which deletes the just-created row and marks the job failed (mapped to 409 by
    # wait_for_install).
    resp = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install/from-repo",
            json={"repo_url": bad},
            headers=platform_admin.headers,
        ),
        platform_admin.headers,
    )
    assert resp.status_code == 409, resp.text
    assert "manifest invalid" in resp.text

    # The failed job deleted its newly-created install row.
    listing = e2e_client.get("/api/solutions", headers=platform_admin.headers)
    assert listing.status_code == 200, listing.text
    assert not [s for s in listing.json()["solutions"] if s["slug"] == slug]
