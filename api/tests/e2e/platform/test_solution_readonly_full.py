"""Exhaustive read-only enforcement: EVERY non-deploy mutation surface on a
solution-managed entity must refuse with 409 (criterion 6).

This complements test_solution_readonly.py (which covers workflow PATCH/DELETE)
by hitting the secondary mutation paths an earlier audit found unguarded:
workflow orphan ops + role assign/remove; app publish/replace/logo/dependencies
+ app source file write/delete (these write S3 and bypass the ORM backstop).
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e

_MSG = "Solution-managed entities can only be managed by deployment methods."


def _solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
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
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text
    # Deploy remaps each manifest id to uuid5(install_id, manifest_id); the entity
    # is addressable only by the remapped id.
    real_wf = str(solution_entity_id(UUID(sid), UUID(wf_id)))
    real_app = str(solution_entity_id(UUID(sid), UUID(app_id)))
    return real_wf, real_app


def test_solution_managed_trigger_is_locked(e2e_client, platform_admin):
    """A deployed schedule trigger (EventSource) is read-only: PATCH/DELETE on
    the source must 409 (deploy is the only writer; uninstall removes it)."""
    headers = platform_admin.headers
    sid = _solution(e2e_client, headers, f"rofull-evt-{uuid.uuid4().hex[:8]}")
    wf_id = str(uuid.uuid4())
    es_id = str(uuid.uuid4())
    slug = uuid.uuid4().hex[:8]
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {"workflows/w.py": "from bifrost import workflow\n@workflow\nasync def w():\n    return {}\n"},
        "workflows": [{
            "id": wf_id, "name": f"w_{slug}", "function_name": "w",
            "path": "workflows/w.py", "type": "workflow",
        }],
        "events": [{
            "id": es_id, "name": f"nightly_{slug}", "source_type": "schedule",
            "cron_expression": "0 9 * * *", "timezone": "UTC",
            "subscriptions": [{
                "id": str(uuid.uuid4()), "target_type": "workflow",
                "workflow_id": wf_id,
            }],
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text
    real_es = str(solution_entity_id(UUID(sid), UUID(es_id)))

    patch = e2e_client.patch(
        f"/api/events/sources/{real_es}", headers=headers, json={"name": "hijack"}
    )
    assert patch.status_code == 409, f"{patch.status_code} {patch.text}"
    assert _MSG in patch.json()["detail"]

    delete = e2e_client.delete(f"/api/events/sources/{real_es}", headers=headers)
    assert delete.status_code == 409, f"{delete.status_code} {delete.text}"


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


def test_remap_skips_managed_form_and_agent_tool(e2e_client, platform_admin):
    """POST /api/workflows/{id}/remap repoints workflow references. It uses a Core
    update() for Form.workflow_id (bypassing the ORM before_flush backstop) and
    junction-ORM for AgentTool.workflow_id. Neither path may rewrite a
    solution-managed Form/Agent's binding outside deploy (criterion 6). The remap
    must SKIP managed rows and NOT over-report them as 'updated'.
    """
    from tests.e2e.conftest import write_and_register

    headers = platform_admin.headers
    sid = _solution(e2e_client, headers, f"remap-{uuid.uuid4().hex[:8]}")

    # Deploy a managed workflow + a managed form bound to it + a managed agent
    # whose tool is bound to it.
    wf_id = str(uuid.uuid4())
    form_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    slug = uuid.uuid4().hex[:8]
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {"workflows/w.py": "from bifrost import workflow\n@workflow\nasync def w():\n    return {}\n"},
        "workflows": [{
            "id": wf_id, "name": f"w_{slug}", "function_name": "w",
            "path": "workflows/w.py", "type": "workflow",
        }],
        "forms": [{
            "id": form_id, "name": f"f_{slug}", "path": f"forms/{form_id}.form.yaml",
            "workflow_id": wf_id, "form_schema": {"fields": []},
        }],
        "agents": [{
            "id": agent_id, "name": f"a_{slug}",
            "system_prompt": "x", "tool_ids": [wf_id],
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text

    real_wf = str(solution_entity_id(UUID(sid), UUID(wf_id)))
    real_form = str(solution_entity_id(UUID(sid), UUID(form_id)))
    real_agent = str(solution_entity_id(UUID(sid), UUID(agent_id)))

    # A legit, mutable _repo/ target of the same type to remap onto.
    tfn = f"remap_target_{uuid.uuid4().hex[:8]}"
    target = write_and_register(
        e2e_client, headers,
        path=f"workflows/{tfn}.py",
        content=f"from bifrost import workflow\n\n@workflow\nasync def {tfn}() -> dict:\n    return {{}}\n",
        function_name=tfn,
    )

    resp = e2e_client.post(f"/api/workflows/{real_wf}/remap", headers=headers,
                           json={"target_workflow_id": target["id"]})
    assert resp.status_code == 200, resp.text
    updated = resp.json()["updated"]
    # The managed form + managed agent tool must NOT be counted as updated.
    assert updated["forms"] == 0, updated
    assert updated["agents"] == 0, updated

    # The managed form's workflow_id is UNCHANGED (still points at the managed wf).
    fresp = e2e_client.get(f"/api/forms/{real_form}", headers=headers)
    assert fresp.status_code == 200, fresp.text
    assert fresp.json()["workflow_id"] == real_wf, fresp.json()

    # The managed agent still has its tool bound to the managed workflow.
    aresp = e2e_client.get(f"/api/agents/{real_agent}", headers=headers)
    assert aresp.status_code == 200, aresp.text
    assert real_wf in aresp.json().get("tool_ids", []), aresp.json()


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
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text

    # Deleting the role now would cascade-remove the managed binding → refuse.
    resp = e2e_client.delete(f"/api/roles/{role_id}", headers=headers)
    assert resp.status_code == 409, f"{resp.status_code} {resp.text}"
    detail = resp.json()["detail"].lower()
    # The error must explain it's solution-managed AND name the owning install so
    # the operator knows which to redeploy (audit M-ROLE).
    assert "solution install" in detail, detail
    assert "redeploy" in detail, detail


def test_delete_role_used_only_by_unmanaged_entities_succeeds(e2e_client, platform_admin):
    """The block must not over-fire: a role bound ONLY to ad-hoc (_repo/)
    entities — or to nothing — deletes normally (audit M-ROLE: don't over-block
    shared/ordinary roles)."""
    headers = platform_admin.headers
    role_name = f"r-{uuid.uuid4().hex[:6]}"
    r = e2e_client.post("/api/roles", headers=headers, json={"name": role_name})
    assert r.status_code in (200, 201), r.text
    role_id = r.json()["id"]

    # No managed binding exists for this role → delete is allowed.
    resp = e2e_client.delete(f"/api/roles/{role_id}", headers=headers)
    assert resp.status_code in (200, 204), f"{resp.status_code} {resp.text}"
