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
