"""End-to-end: a git-connected install REFUSES deploy (one-writer invariant,
criterion 13). Auto-pull is the only writer for a connected install; the deploy
endpoint (and thus `bifrost deploy`) must return an error."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def test_connected_install_refuses_deploy(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"gitconn-{uuid.uuid4().hex[:8]}"

    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
        "git_connected": True, "git_repo_url": "https://example.com/x.git",
    })
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {}, "workflows": [],
    })
    assert dep.status_code == 409, dep.text
    assert "git-connected" in dep.json()["detail"].lower() or "disabled" in dep.json()["detail"].lower()


def test_disconnected_install_allows_deploy(e2e_client, platform_admin):
    """Control: a disconnected install deploys normally (no regression)."""
    headers = platform_admin.headers
    slug = f"disc-{uuid.uuid4().hex[:8]}"
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {}, "workflows": [],
    })
    assert dep.status_code in (200, 201), dep.text


async def test_concurrent_deploy_to_same_install_is_refused(e2e_client, platform_admin):
    """Codex #12: the deploy endpoint holds a per-install write lock across the
    DB commit AND the S3 finalize, so a second concurrent deploy to the SAME
    install is refused (409) rather than interleaving its finalize. Deterministic
    repro: hold the lock out-of-band, then POST deploy → 409."""
    from uuid import UUID

    from src.services.solutions.write_lock import solution_write_lock

    headers = platform_admin.headers
    slug = f"conc-{uuid.uuid4().hex[:8]}"
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]

    # Simulate an in-flight deploy by holding the install's write lock.
    async with solution_write_lock(UUID(sid)):
        dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
            "python_files": {}, "workflows": [],
        })
    assert dep.status_code == 409, dep.text
    assert "in progress" in dep.text.lower()

    # Lock released → a subsequent deploy succeeds (not wedged).
    dep2 = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {}, "workflows": [],
    })
    assert dep2.status_code in (200, 201), dep2.text


def test_invalid_bundle_returns_409_not_500(e2e_client, platform_admin):
    """Codex #13: a deploy rejected by SolutionDeployConflict (here: an
    inline_v1 app, which Solution deploy refuses) surfaces as a 409 with the
    reason, NOT an unhandled 500."""
    headers = platform_admin.headers
    slug = f"badbundle-{uuid.uuid4().hex[:8]}"
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "apps": [{
            "id": str(uuid.uuid4()), "slug": "legacy", "name": "Legacy",
            "app_model": "inline_v1",
        }],
    })
    assert dep.status_code == 409, f"expected 409, got {dep.status_code}: {dep.text}"
    assert "standalone_v2" in dep.text
