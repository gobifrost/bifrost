"""End-to-end: a git-connected install REFUSES deploy (one-writer invariant,
criterion 13). Auto-pull is the only writer for a connected install; the deploy
endpoint (and thus `bifrost deploy`) must return an error."""
from __future__ import annotations

import uuid

import pytest

from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def test_connected_install_refuses_deploy(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"gitconn-{uuid.uuid4().hex[:8]}"

    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
        "git_connected": True, "git_repo_url": "https://example.com/x.git",
    })
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {}, "workflows": [],
    })
    assert dep.status_code == 409, dep.text
    assert "git-connected" in dep.json()["detail"].lower() or "disabled" in dep.json()["detail"].lower()


async def test_concurrent_deploy_to_same_install_is_refused(e2e_client, platform_admin):
    """Codex #12: the deploy holds a per-install write lock across the DB commit
    AND the S3 finalize, so a second concurrent deploy to the SAME install is
    refused rather than interleaving its finalize. Under async deploy the lock is
    held inside the background job, so a contended deploy reaches a FAILED job
    with an "in progress" error (surfaced by ``wait_for_deploy`` as a 422 shim).
    Deterministic repro: hold the lock out-of-band, then deploy → failed."""
    from uuid import UUID

    from src.services.solutions.write_lock import solution_write_lock

    headers = platform_admin.headers
    slug = f"conc-{uuid.uuid4().hex[:8]}"
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]

    # Simulate an in-flight deploy by holding the install's write lock. The POST
    # is accepted (202) but the background job can't get the lock → fails.
    async with solution_write_lock(UUID(sid)):
        dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
            "python_files": {}, "workflows": [],
        })
        dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 422, dep.text
    assert "in progress" in dep.text.lower()
