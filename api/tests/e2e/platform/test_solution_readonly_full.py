"""Exhaustive read-only enforcement: EVERY non-deploy mutation surface on a
solution-managed entity must refuse with 409 (criterion 6).

This complements test_solution_readonly.py (which covers workflow PATCH/DELETE)
by hitting the secondary mutation paths an earlier audit found unguarded:
workflow orphan ops + role assign/remove; app publish/replace/logo/dependencies
+ app source file write/delete (these write S3 and bypass the ORM backstop).
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e

_MSG = "Solution-managed entities can only be managed by deployment methods."


def _solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _deploy_workflow_and_app(e2e_client, headers, sid: str):
    wf_id = str(uuid.uuid4())
    app_id = str(uuid.uuid4())
    slug = uuid.uuid4().hex[:8]
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {"workflows/w.py": "from bifrost import workflow\n@workflow\nasync def w():\n    return {}\n"},
        "workflows": [{
            "id": wf_id, "name": f"w_{slug}", "function_name": "w",
            "path": "workflows/w.py", "type": "workflow",
        }],
        "apps": [{
            "id": app_id, "slug": f"app-{slug}", "name": "App",
            "app_model": "standalone_v2", "dependencies": {},
            "dist_files": {"index.html": "<html></html>"},
        }],
    })
    assert dep.status_code in (200, 201), dep.text
    return wf_id, app_id


def test_workflow_secondary_mutations_are_locked(e2e_client, platform_admin):
    headers = platform_admin.headers
    sid = _solution(e2e_client, headers, f"rofull-wf-{uuid.uuid4().hex[:8]}")
    wf_id, _ = _deploy_workflow_and_app(e2e_client, headers, sid)
    role_id = str(uuid.uuid4())

    # Each of these is a non-deploy mutation surface → must 409 with the message.
    cases = [
        ("post", f"/api/workflows/{wf_id}/replace", {"source_path": "workflows/x.py", "function_name": "w"}),
        ("post", f"/api/workflows/{wf_id}/recreate", {}),
        ("post", f"/api/workflows/{wf_id}/deactivate", {}),
        ("post", f"/api/workflows/{wf_id}/roles", {"role_ids": [role_id]}),
        ("delete", f"/api/workflows/{wf_id}/roles/{role_id}", None),
    ]
    for method, path, body in cases:
        fn = getattr(e2e_client, method)
        resp = fn(path, headers=headers, json=body) if body is not None else fn(path, headers=headers)
        assert resp.status_code == 409, f"{method.upper()} {path} -> {resp.status_code} {resp.text}"
        assert resp.json()["detail"] == _MSG, f"{method.upper()} {path}: {resp.json()}"


def test_app_secondary_mutations_are_locked(e2e_client, platform_admin):
    headers = platform_admin.headers
    sid = _solution(e2e_client, headers, f"rofull-app-{uuid.uuid4().hex[:8]}")
    _, app_id = _deploy_workflow_and_app(e2e_client, headers, sid)

    cases = [
        ("post", f"/api/applications/{app_id}/publish", {}),
        ("post", f"/api/applications/{app_id}/replace", {"repo_path": "apps/moved", "force": True}),
        ("post", f"/api/applications/{app_id}/rollback", {"version_id": str(uuid.uuid4())}),
        # App source file write + delete (these bypass the ORM backstop — S3 writes).
        ("put", f"/api/applications/{app_id}/files/pages/index.tsx", {"source": "export default 1"}),
        ("delete", f"/api/applications/{app_id}/files/pages/index.tsx", None),
        ("put", f"/api/applications/{app_id}/dependencies", {"left-pad": "1.0.0"}),
    ]
    for method, path, body in cases:
        fn = getattr(e2e_client, method)
        resp = fn(path, headers=headers, json=body) if body is not None else fn(path, headers=headers)
        assert resp.status_code == 409, f"{method.upper()} {path} -> {resp.status_code} {resp.text}"
        assert resp.json()["detail"] == _MSG, f"{method.upper()} {path}: {resp.json()}"


def test_role_endpoints_locked_for_managed_workflow(e2e_client, platform_admin):
    """Role-centric endpoints (/api/roles/{id}/workflows) must refuse to mutate
    role bindings on a solution-managed entity (Codex P1-a)."""
    headers = platform_admin.headers
    sid = _solution(e2e_client, headers, f"role-{uuid.uuid4().hex[:8]}")
    wf_id, _ = _deploy_workflow_and_app(e2e_client, headers, sid)
    # Create a role to assign.
    r = e2e_client.post("/api/roles", headers=headers, json={"name": f"r-{uuid.uuid4().hex[:6]}"})
    assert r.status_code in (200, 201), r.text
    role_id = r.json()["id"]

    # Assign the managed workflow to the role → must 409.
    resp = e2e_client.post(f"/api/roles/{role_id}/workflows", headers=headers,
                           json={"workflow_ids": [wf_id]})
    assert resp.status_code == 409, f"{resp.status_code} {resp.text}"
    assert resp.json()["detail"] == _MSG


def test_delete_role_bound_to_managed_entity_is_refused(e2e_client, platform_admin):
    """DELETE /api/roles/{id} cascades through the *_roles junctions; deleting a
    role assigned to a solution-managed entity would strip deploy-owned bindings
    outside deploy. It must be refused (Codex R4)."""
    headers = platform_admin.headers
    sid = _solution(e2e_client, headers, f"roledel-{uuid.uuid4().hex[:8]}")

    # Create a role, then DEPLOY a role_based workflow bound to it (deploy is the
    # only writer of managed role bindings).
    role_name = f"r-{uuid.uuid4().hex[:6]}"
    r = e2e_client.post("/api/roles", headers=headers, json={"name": role_name})
    assert r.status_code in (200, 201), r.text
    role_id = r.json()["id"]

    wf_id = str(uuid.uuid4())
    slug = uuid.uuid4().hex[:8]
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {"workflows/w.py": "from bifrost import workflow\n@workflow\nasync def w():\n    return {}\n"},
        "workflows": [{
            "id": wf_id, "name": f"w_{slug}", "function_name": "w",
            "path": "workflows/w.py", "type": "workflow",
            "access_level": "role_based", "role_names": [role_name],
        }],
    })
    assert dep.status_code in (200, 201), dep.text

    # Deleting the role now would cascade-remove the managed binding → refuse.
    resp = e2e_client.delete(f"/api/roles/{role_id}", headers=headers)
    assert resp.status_code == 409, f"{resp.status_code} {resp.text}"
    assert "solution-managed" in resp.json()["detail"].lower()
