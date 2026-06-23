"""E2E tests for repo dirty flag and repo-status endpoint."""

from tests.e2e.file_policy_helpers import grant_file_policy


def test_repo_status_default(e2e_client, platform_admin):
    """Repo status endpoint should return expected shape."""
    resp = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "dirty" in data
    assert "dirty_since" in data


def test_repo_status_dirty_after_editor_write(e2e_client, platform_admin):
    """Writing via the editor endpoint should mark repo dirty."""
    e2e_client.put("/api/files/editor/content", headers=platform_admin.headers, json={
        "path": "test-dirty-flag.py",
        "content": "# test dirty flag",
    })
    resp = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["dirty"] is True
    assert data["dirty_since"] is not None


def test_cli_push_does_not_set_dirty(e2e_client, platform_admin):
    """CLI per-file write should not mark repo as dirty."""
    grant_file_policy(e2e_client, platform_admin.headers, location="workspace")

    # Get current dirty state
    before = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers).json()

    # Push a file via per-file write endpoint
    resp = e2e_client.post("/api/files/write", headers=platform_admin.headers, json={
        "path": "test-push-no-dirty.py",
        "content": "# test push",
        "mode": "cloud",
        "location": "workspace",
    })
    assert resp.status_code == 204

    # If it was clean before, it should still be clean
    after = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers).json()
    if not before["dirty"]:
        assert after["dirty"] is False
