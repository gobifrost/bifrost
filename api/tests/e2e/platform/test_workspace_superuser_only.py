"""The `workspace` location uses repository Roles, not file policies.

Regression guard for the CLI `sync`/`watch` 403: the file-policies feature
made `/api/files/list` and `/api/files/read` evaluate file policies even for
`location="workspace"`. With no policy row present (the normal case — nobody
sets policies on the shared codebase), the policy service default-denies, so a
superuser running `bifrost sync` got a 403. The CLI swallowed that and reported
every local file as "new locally" → a mass push.

`workspace` is the shared platform codebase: it must be reachable by Platform
Admin and by a person with repository capability at the Platform boundary,
without a file-policy row. These tests assert that directly.
"""
import hashlib
from uuid import uuid4

import pytest


@pytest.fixture
def repository_builder(e2e_client, platform_admin, org1_user, org1):
    platform_headers = {
        **platform_admin.headers,
        "X-Bifrost-Boundary": "platform",
    }
    role_response = e2e_client.post(
        "/api/roles",
        headers=platform_headers,
        json={
            "name": f"Repository Builder {uuid4().hex[:8]}",
            "description": "Repository authorization E2E",
            "capabilities": ["repository.readwrite"],
        },
    )
    assert role_response.status_code == 201, role_response.text
    role_id = role_response.json()["id"]
    assigned = e2e_client.post(
        f"/api/roles/{role_id}/users",
        headers=platform_headers,
        json={
            "user_ids": [str(org1_user.user_id)],
            "boundaries": [{"boundary_kind": "platform"}],
        },
    )
    assert assigned.status_code == 204, assigned.text
    yield {
        "platform": {
            **org1_user.headers,
            "X-Bifrost-Boundary": "platform",
        },
        "organization": {
            **org1_user.headers,
            "X-Bifrost-Boundary": f"organization:{org1['id']}",
        },
    }
    e2e_client.delete(
        f"/api/roles/{role_id}/users/{org1_user.user_id}",
        headers=platform_headers,
    )
    e2e_client.delete(f"/api/roles/{role_id}", headers=platform_headers)


def _write(e2e_client, headers, path, content):
    return e2e_client.post("/api/files/write", headers=headers, json={
        "path": path,
        "content": content,
        "mode": "cloud",
        "location": "workspace",
        "binary": False,
    })


def test_superuser_lists_workspace_without_policy(e2e_client, platform_admin):
    """A superuser can list the workspace with no file policy granted."""
    resp = e2e_client.post("/api/files/list", headers=platform_admin.headers, json={
        "include_metadata": True,
        "mode": "cloud",
        "location": "workspace",
    })
    assert resp.status_code == 200, f"workspace list denied: {resp.status_code} {resp.text}"


def test_superuser_write_then_read_workspace_without_policy(e2e_client, platform_admin):
    """A superuser can write and read back a workspace file with no policy."""
    path = "modules/_ws_superuser_probe.py"
    content = "# workspace superuser probe\n"
    w = _write(e2e_client, platform_admin.headers, path, content)
    assert w.status_code == 204, f"workspace write denied: {w.status_code} {w.text}"

    r = e2e_client.post("/api/files/read", headers=platform_admin.headers, json={
        "path": path,
        "mode": "cloud",
        "location": "workspace",
        "binary": False,
    })
    assert r.status_code == 200, f"workspace read denied: {r.status_code} {r.text}"
    assert r.json()["content"] == content


def test_superuser_patch_is_version_guarded_and_persists(e2e_client, platform_admin):
    """The canonical patch route performs one unique, conflict-safe edit."""
    path = "modules/_ws_patch_probe.py"
    assert _write(
        e2e_client,
        platform_admin.headers,
        path,
        "value = 'old'\n",
    ).status_code == 204

    stat = e2e_client.post(
        "/api/files/stat",
        headers=platform_admin.headers,
        json={"path": path, "mode": "cloud", "location": "workspace"},
    )
    assert stat.status_code == 200, stat.text

    patched = e2e_client.post(
        "/api/files/patch",
        headers=platform_admin.headers,
        json={
            "path": path,
            "old_string": "'old'",
            "new_string": "'new'",
            "expected_version": stat.json()["version"],
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["lines_changed"] == 1
    assert patched.json()["version"] != stat.json()["version"]

    read = e2e_client.post(
        "/api/files/read",
        headers=platform_admin.headers,
        json={"path": path, "mode": "cloud", "location": "workspace"},
    )
    assert read.status_code == 200, read.text
    assert read.json()["content"] == "value = 'new'\n"

    stale = e2e_client.post(
        "/api/files/patch",
        headers=platform_admin.headers,
        json={
            "path": path,
            "old_string": "'new'",
            "new_string": "'stale'",
            "expected_version": stat.json()["version"],
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["reason"] == "version_conflict"


def test_superuser_list_metadata_etag_matches_local_md5(e2e_client, platform_admin):
    """The metadata listing returns plain-MD5 etags the CLI diff relies on."""
    path = "modules/_ws_etag_probe.py"
    content = "print('etag')\n"
    assert _write(e2e_client, platform_admin.headers, path, content).status_code == 204

    resp = e2e_client.post("/api/files/list", headers=platform_admin.headers, json={
        "include_metadata": True,
        "mode": "cloud",
        "location": "workspace",
    })
    assert resp.status_code == 200, resp.text
    meta = {m["path"]: m for m in resp.json()["files_metadata"]}
    assert path in meta, f"{path} not in listing"
    expected = hashlib.md5(content.encode()).hexdigest()
    assert meta[path]["etag"] == expected


def test_non_superuser_denied_workspace(e2e_client, non_admin_user):
    """A person without repository capability cannot touch the workspace."""
    resp = e2e_client.post("/api/files/list", headers=non_admin_user.headers, json={
        "include_metadata": True,
        "mode": "cloud",
        "location": "workspace",
    })
    assert resp.status_code == 403, f"expected 403 for non-admin, got {resp.status_code}"


def test_repository_role_can_use_complete_workspace_contract(
    e2e_client,
    repository_builder,
):
    """A Platform-scoped repository Role unlocks the same Builder/CLI surface."""
    headers = repository_builder["platform"]
    marker = uuid4().hex
    path = f"modules/_repository_builder_{marker}.py"
    content = f"repository_marker = '{marker}'\n"

    written = _write(e2e_client, headers, path, content)
    assert written.status_code == 204, written.text
    try:
        listed = e2e_client.post(
            "/api/files/list",
            headers=headers,
            json={
                "mode": "cloud",
                "location": "workspace",
                "include_metadata": True,
            },
        )
        assert listed.status_code == 200, listed.text
        assert path in listed.json()["files"]

        read = e2e_client.post(
            "/api/files/read",
            headers=headers,
            json={"path": path, "mode": "cloud", "location": "workspace"},
        )
        assert read.status_code == 200, read.text
        assert read.json()["content"] == content

        stat = e2e_client.post(
            "/api/files/stat",
            headers=headers,
            json={"path": path, "mode": "cloud", "location": "workspace"},
        )
        assert stat.status_code == 200, stat.text
        assert stat.json()["exists"] is True

        exists = e2e_client.post(
            "/api/files/exists",
            headers=headers,
            json={"path": path, "mode": "cloud", "location": "workspace"},
        )
        assert exists.status_code == 200, exists.text
        assert exists.json()["exists"] is True

        patched = e2e_client.post(
            "/api/files/patch",
            headers=headers,
            json={
                "path": path,
                "old_string": "repository_marker",
                "new_string": "updated_repository_marker",
                "expected_version": stat.json()["version"],
            },
        )
        assert patched.status_code == 200, patched.text

        searched = e2e_client.post(
            "/api/files/search",
            headers=headers,
            json={"query": marker, "include_pattern": path},
        )
        assert searched.status_code == 200, searched.text
        assert any(row["file_path"] == path for row in searched.json()["results"])
    finally:
        deleted = e2e_client.post(
            "/api/files/delete",
            headers=headers,
            json={"path": path, "mode": "cloud", "location": "workspace"},
        )
        assert deleted.status_code == 204, deleted.text


def test_repository_role_does_not_leak_into_organization_boundary(
    e2e_client,
    repository_builder,
):
    denied = e2e_client.post(
        "/api/files/list",
        headers=repository_builder["organization"],
        json={"mode": "cloud", "location": "workspace"},
    )
    assert denied.status_code == 403, denied.text


def test_access_test_endpoint_reports_workspace_repository_capability(e2e_client, platform_admin):
    """The Test Access endpoint mirrors repository Role enforcement."""
    resp = e2e_client.post(
        "/api/files/policies/test",
        headers=platform_admin.headers,
        json={"path": "modules/anything.py", "location": "workspace", "action": "read"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["allowed"] is True
    assert body["matched_policy"] is None
    assert body["matched_rule"] == "repository.read at Platform boundary"
