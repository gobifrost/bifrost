"""End-to-end: create a Solution install, deploy a workflow bundle via REST,
and run the workflow — proving it executes side-by-side with _repo/ and resolves
its own solution-local imports.

Proves (live, against the running stack):
- criterion 2: a Solution deploys and runs concurrently with _repo/.
- criterion 3: a workflow imports its own modules/* from the solution root.
- criterion 4: with global_repo_access OFF, a `shared.*` _repo/ import does NOT
  resolve (no silent fallback).
- criterion 16: end users see only the deployed entity (a normal workflow).
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(
    e2e_client, headers, *, slug: str, global_repo_access: bool, org_id: str | None = None
) -> str:
    body = {
        "slug": slug,
        "name": slug.upper(),
        "global_repo_access": global_repo_access,
    }
    if org_id is None:
        body["scope"] = "global"
    else:
        body["organization_id"] = org_id
    resp = e2e_client.post("/api/solutions", headers=headers, json=body)
    assert resp.status_code in (200, 201), f"create solution failed: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def _deploy(e2e_client, headers, solution_id: str, *, python_files: dict, workflows: list) -> dict:
    from tests.e2e.platform.conftest import deploy_solution

    resp = deploy_solution(
        e2e_client,
        solution_id,
        headers,
        {"python_files": python_files, "workflows": workflows},
    )
    assert resp.status_code in (200, 201), f"deploy failed: {resp.status_code} {resp.text}"
    return resp.json()


def test_deploy_and_run_solution_local_import(e2e_client, platform_admin):
    """A solution workflow imports its own modules/* and runs (criteria 2,3)."""
    from tests.e2e.conftest import execute_workflow_sync

    headers = platform_admin.headers
    slug = f"sol-import-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug, global_repo_access=False)

    wf_id = str(uuid.uuid4())
    _deploy(
        e2e_client,
        headers,
        sid,
        python_files={
            "modules/calc.py": "VALUE = 42\n",
            "workflows/answer.py": (
                "from modules.calc import VALUE\n"
                "from bifrost import workflow\n\n"
                "@workflow\n"
                "async def answer():\n"
                "    return {'value': VALUE}\n"
            ),
        },
        workflows=[{
            "id": wf_id,
            "name": f"answer_{slug}",
            "function_name": "answer",
            "path": "workflows/answer.py",
            "type": "workflow",
        }],
    )

    # Execute by PORTABLE path::fn ref (what a v2 app / form uses) — the deployed
    # row id is remapped per-install (uuid5), so the manifest UUID is not a valid
    # execution handle; the path ref resolves within the install's scope (R7-P1-c).
    result = execute_workflow_sync(
        e2e_client, headers, "workflows/answer.py::answer", request_sync=True
    )
    assert result["status"] == "Success", f"unexpected: {result}"
    assert result["result"] == {"value": 42}


def test_deploy_and_run_when_name_diverges_from_function(e2e_client, platform_admin):
    """Regression for the "Executable 'hello' not found" bug.

    Deploy a workflow whose manifest ``name`` differs from BOTH the decorator
    display name AND the Python ``function_name``. Execution must still run it —
    resolution is by ``function_name`` (service.py / module_loader.py), and the
    DB ``name`` is identity/display only. Before the fix, execution matched the
    decorator display name against the DB name and raised "Executable not found".
    """
    from tests.e2e.conftest import execute_workflow_sync

    headers = platform_admin.headers
    slug = f"sol-namediv-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug, global_repo_access=False)

    _deploy(
        e2e_client,
        headers,
        sid,
        python_files={
            "workflows/snap.py": (
                "from bifrost import workflow\n\n"
                '@workflow(name="Sandbox Ticket Snapshot")\n'  # decorator display name
                "async def snapshot():\n"  # function_name = "snapshot"
                "    return {'ok': True}\n"
            ),
        },
        workflows=[{
            "id": str(uuid.uuid4()),
            "name": "hello",  # manifest name diverges from decorator AND function
            "function_name": "snapshot",
            "path": "workflows/snap.py",
            "type": "workflow",
        }],
    )

    result = execute_workflow_sync(
        e2e_client, headers, "workflows/snap.py::snapshot", request_sync=True
    )
    assert result["status"] == "Success", f"name-divergent workflow failed to run: {result}"
    assert result["result"] == {"ok": True}


def test_global_repo_import_blocked_when_flag_off(e2e_client, platform_admin):
    """With global_repo_access OFF, importing a _repo/ `shared.*` module must
    NOT resolve — no silent fallback (criterion 4)."""
    from tests.e2e.conftest import execute_workflow_sync

    headers = platform_admin.headers
    slug = f"sol-noglobal-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug, global_repo_access=False)

    wf_id = str(uuid.uuid4())
    _deploy(
        e2e_client,
        headers,
        sid,
        python_files={
            "workflows/needs_shared.py": (
                "import shared.definitely_not_in_solution  # noqa\n"
                "from bifrost import workflow\n\n"
                "@workflow\n"
                "async def go():\n"
                "    return 1\n"
            ),
        },
        workflows=[{
            "id": wf_id,
            "name": f"needs_shared_{slug}",
            "function_name": "go",
            "path": "workflows/needs_shared.py",
            "type": "workflow",
        }],
    )

    result = execute_workflow_sync(
        e2e_client, headers, "workflows/needs_shared.py::go", request_sync=True
    )
    assert result["status"] == "Failed", f"expected import failure, got: {result}"
    blob = f"{result.get('error')} {result.get('error_type')}".lower()
    assert "module" in blob or "import" in blob, f"unexpected error: {result}"


def _execute_with_app(e2e_client, headers, workflow_ref: str, app_id: str) -> dict:
    """POST /api/workflows/execute with an app_id scope, sync, return the result."""
    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=headers,
        json={"workflow_id": workflow_ref, "app_id": app_id, "sync": True},
    )
    assert resp.status_code == 200, f"execute failed: {resp.status_code} {resp.text}"
    return resp.json()


def _deploy_install_with_app(e2e_client, headers, marker: str, org_id: str | None = None) -> str:
    """Deploy a Solution install shipping workflows/main.py::main (returns the
    marker) plus a standalone_v2 app; return the app's remapped DB id."""
    from tests.e2e.platform.conftest import deploy_solution

    slug = f"twin-{marker}-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(
        e2e_client, headers, slug=slug, global_repo_access=False, org_id=org_id
    )
    app_id = str(uuid.uuid4())
    # Deploy is ASYNC (BackgroundTasks) — fire-and-forget would race the
    # background job, so the immediately-following execute can 404 on a
    # workflow whose row hasn't committed yet (flaky under load). Use the
    # deploy_solution helper that blocks until the deploy job is terminal.
    resp = deploy_solution(
        e2e_client,
        sid,
        headers,
        {
            "python_files": {
                "workflows/main.py": (
                    "from bifrost import workflow\n\n"
                    "@workflow\n"
                    "async def main():\n"
                    f"    return {{'marker': '{marker}'}}\n"
                ),
            },
            "workflows": [{
                "id": str(uuid.uuid4()),
                "name": f"main_{slug}",
                "function_name": "main",
                "path": "workflows/main.py",
                "type": "workflow",
            }],
            "apps": [{
                "id": app_id,
                "slug": f"app-{slug}",
                "name": "App",
                "app_model": "standalone_v2",
                "dependencies": {},
                "access_level": "authenticated",
                "dist_files": {
                    "index.html": '<!doctype html><div id="root"></div>',
                },
            }],
        },
    )
    assert resp.status_code in (200, 201), f"deploy failed: {resp.status_code} {resp.text}"
    # The app's DB id is the remapped uuid5(install, manifest_id).
    from src.services.solutions.deploy import solution_entity_id

    return str(solution_entity_id(uuid.UUID(sid), uuid.UUID(app_id)))


def test_two_installs_same_path_resolve_own_workflow_via_app_id(e2e_client, platform_admin):
    """Codex #8 P1 end-to-end: two Solution installs each ship
    workflows/main.py::main (different return values) AND an app. Executing each
    app's workflow ref with that app's app_id resolves THAT install's own
    workflow — deterministically, not a sibling install's that shares the path."""
    headers = platform_admin.headers

    app_a = _deploy_install_with_app(e2e_client, headers, "aaa")
    app_b = _deploy_install_with_app(e2e_client, headers, "bbb")

    # Each app's path-ref resolves to ITS OWN install's workflow.
    res_a = _execute_with_app(e2e_client, headers, "workflows/main.py::main", app_a)
    res_b = _execute_with_app(e2e_client, headers, "workflows/main.py::main", app_b)
    assert res_a["status"] == "Success", res_a
    assert res_b["status"] == "Success", res_b
    assert res_a["result"] == {"marker": "aaa"}, res_a
    assert res_b["result"] == {"marker": "bbb"}, res_b


def test_app_header_alone_scopes_workflow_execution(e2e_client, platform_admin):
    """The deployed-browser transport contract: X-Bifrost-App header, NO body
    app_id. Auth derives ctx.solution_id from the header; workflow execution
    must honor that context scope so a path::fn ref resolves the install's own
    workflow — same as tables/files already do."""
    headers = platform_admin.headers

    app_a = _deploy_install_with_app(e2e_client, headers, "hdr-aaa")
    app_b = _deploy_install_with_app(e2e_client, headers, "hdr-bbb")

    def _execute_with_header(app_id: str) -> dict:
        resp = e2e_client.post(
            "/api/workflows/execute",
            headers={**headers, "X-Bifrost-App": app_id},
            json={"workflow_id": "workflows/main.py::main", "sync": True},
        )
        assert resp.status_code == 200, f"execute failed: {resp.status_code} {resp.text}"
        return resp.json()

    res_a = _execute_with_header(app_a)
    res_b = _execute_with_header(app_b)
    assert res_a["status"] == "Success", res_a
    assert res_b["status"] == "Success", res_b
    assert res_a["result"] == {"marker": "hdr-aaa"}, res_a
    assert res_b["result"] == {"marker": "hdr-bbb"}, res_b


def test_workflow_404_includes_scope_diagnostics(e2e_client, platform_admin):
    """A scope-resolution miss must identify itself: the 404 detail carries the
    ref and the derived install scope, so a dropped/wrong scope reads as
    `derived_solution_scope: null` instead of a mystery 404 (drive lesson —
    the unscoped courtesy fallback masked scope loss for a whole POC day)."""
    headers = platform_admin.headers
    app_a = _deploy_install_with_app(e2e_client, headers, "diag")

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers={**headers, "X-Bifrost-App": app_a},
        json={"workflow_id": "workflows/nonexistent.py::nope", "sync": True},
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    assert detail["workflow_ref"] == "workflows/nonexistent.py::nope"
    assert "not found" in detail["message"]
    # Header-scoped caller: the derived install scope is present (a UUID).
    assert detail["derived_solution_scope"], detail


def test_admin_resolves_orgbound_install_path_ref_cross_org(
    e2e_client, platform_admin, org1
):
    """Dev14 regression: an ORG-BOUND install's workflows carry the install's
    org; a platform admin whose effective org differs (the normal demo/support
    posture) must still resolve the install's own workflow by path::fn via the
    app header — superusers bypass the caller-org gate on the OWN-install
    match, exactly like resolve_solution_table_by_name does for tables.
    Global installs never caught this (their rows have organization_id NULL)."""
    headers = platform_admin.headers
    app_a = _deploy_install_with_app(e2e_client, headers, "orgbound", org_id=org1["id"])

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers={**headers, "X-Bifrost-App": app_a},
        json={"workflow_id": "workflows/main.py::main", "sync": True},
    )
    assert resp.status_code == 200, f"execute failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["status"] == "Success", body
    assert body["result"] == {"marker": "orgbound"}, body


def test_foreign_app_header_cannot_reach_other_orgs_workflow(
    e2e_client, platform_admin, org1, org2_user
):
    """PINNING (expected to hold): a regular user from org2 smuggling org1's
    X-Bifrost-App must NOT execute org1's install workflow — the resolver's
    org gate (cascade scope) holds under ctx-first scoping."""
    headers = platform_admin.headers
    app_a = _deploy_install_with_app(e2e_client, headers, "xorg", org_id=org1["id"])

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers={**org2_user.headers, "X-Bifrost-App": app_a},
        json={"workflow_id": "workflows/main.py::main", "sync": True},
    )
    assert resp.status_code in (403, 404), (
        f"cross-org header execution must be refused, got {resp.status_code}: {resp.text}"
    )
