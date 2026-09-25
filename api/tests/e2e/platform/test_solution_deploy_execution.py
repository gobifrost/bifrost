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


def test_solution_deploy_resolves_local_workflows_and_blocks_global_import(
    e2e_client, platform_admin,
):
    """One deploy checks local modules, vendored shared imports, function
    identity, and repo isolation."""
    import asyncio

    from src.services.solutions.deploy import solution_entity_id
    from src.services.solutions.vendoring import vendor_shared_deps
    from tests.e2e.conftest import execute_workflow_sync

    headers = platform_admin.headers
    slug = f"sol-import-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug, global_repo_access=False)
    vendored_workflow_id = uuid.uuid4()

    solution_files = {
        "modules/calc.py": "VALUE = 42\n",
        "workflows/answer.py": (
            "from modules.calc import VALUE\n"
            "from bifrost import workflow\n\n"
            "@workflow\n"
            "async def answer():\n"
            "    return {'value': VALUE}\n"
        ),
        "workflows/uses_shared.py": (
            "from shared.vend_calc import VALUE\n"
            "from bifrost import workflow\n\n"
            "@workflow\n"
            "async def go():\n"
            "    return {'value': VALUE}\n"
        ),
        "workflows/snap.py": (
            "from bifrost import workflow\n\n"
            '@workflow(name="Sandbox Ticket Snapshot")\n'
            "async def snapshot():\n"
            "    return {'ok': True}\n"
        ),
        "workflows/needs_shared.py": (
            "import shared.definitely_not_in_solution  # noqa\n"
            "from bifrost import workflow\n\n"
            "@workflow\n"
            "async def go():\n"
            "    return 1\n"
        ),
    }

    async def repo_read(path: str):
        return {"shared/vend_calc.py": "VALUE = 7\n"}.get(path)

    vendored_files = asyncio.run(vendor_shared_deps(solution_files, repo_read))
    assert vendored_files == {"shared/vend_calc.py": "VALUE = 7\n"}

    _deploy(
        e2e_client,
        headers,
        sid,
        python_files={**solution_files, **vendored_files},
        workflows=[
            {
                "id": str(uuid.uuid4()),
                "name": f"answer_{slug}",
                "function_name": "answer",
                "path": "workflows/answer.py",
                "type": "workflow",
            },
            {
                "id": str(uuid.uuid4()),
                "name": "hello",  # diverges from decorator name and function_name
                "function_name": "snapshot",
                "path": "workflows/snap.py",
                "type": "workflow",
            },
            {
                "id": str(vendored_workflow_id),
                "name": f"uses_shared_{slug}",
                "function_name": "go",
                "path": "workflows/uses_shared.py",
                "type": "workflow",
            },
            {
                "id": str(uuid.uuid4()),
                "name": f"needs_shared_{slug}",
                "function_name": "go",
                "path": "workflows/needs_shared.py",
                "type": "workflow",
            },
        ],
    )

    # Portable path::fn references resolve within this install, even though
    # manifest UUIDs are remapped during deployment.
    local = execute_workflow_sync(
        e2e_client, headers, "workflows/answer.py::answer", request_sync=True
    )
    assert local["status"] == "Success", local
    assert local["result"] == {"value": 42}

    # The vendored module resolves after deploy by the remapped entity ID,
    # even though global_repo_access is disabled.
    vendored = execute_workflow_sync(
        e2e_client,
        headers,
        str(solution_entity_id(uuid.UUID(sid), vendored_workflow_id)),
        request_sync=True,
    )
    assert vendored["status"] == "Success", vendored
    assert vendored["result"] == {"value": 7}

    # Execution uses function_name; the manifest name and decorator display
    # name are intentionally different.
    named = execute_workflow_sync(
        e2e_client, headers, "workflows/snap.py::snapshot", request_sync=True
    )
    assert named["status"] == "Success", named
    assert named["result"] == {"ok": True}

    # global_repo_access=False forbids silently resolving a shared.* import
    # from the workspace repository.
    blocked = execute_workflow_sync(
        e2e_client, headers, "workflows/needs_shared.py::go", request_sync=True
    )
    assert blocked["status"] == "Failed", blocked
    error = f"{blocked.get('error')} {blocked.get('error_type')}".lower()
    assert "module" in error or "import" in error, blocked


def _execute_with_app(e2e_client, headers, workflow_ref: str, app_id: str) -> dict:
    """POST /api/workflows/execute with an app_id scope, sync, return the result."""
    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=headers,
        json={"workflow_id": workflow_ref, "app_id": app_id, "sync": True},
    )
    assert resp.status_code == 200, f"execute failed: {resp.status_code} {resp.text}"
    return resp.json()


def _deploy_install_with_app(e2e_client, headers, marker: str, org_id: str | None = None) -> dict:
    """Deploy a Solution install shipping workflows/main.py::main (returns the
    marker) plus a standalone_v2 app; return {app_id, workflow_name, solution_id}."""
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

    return {
        "app_id": str(solution_entity_id(uuid.UUID(sid), uuid.UUID(app_id))),
        "workflow_name": f"main_{slug}",
        "solution_id": sid,
    }


@pytest.fixture(scope="module")
def orgbound_install(e2e_client, platform_admin, org1) -> dict:
    """One real deploy for independent app-scoped resolution checks."""
    return _deploy_install_with_app(
        e2e_client, platform_admin.headers, "orgbound", org_id=org1["id"]
    )


def test_two_installs_same_path_resolve_own_workflow_via_app_scope(
    e2e_client, platform_admin, orgbound_install,
):
    """Both body app_id and the browser's X-Bifrost-App header resolve the
    workflow from the matching install when two installs share a path."""
    headers = platform_admin.headers

    app_a = orgbound_install["app_id"]
    app_b = _deploy_install_with_app(e2e_client, headers, "bbb")["app_id"]

    # Each app's path-ref resolves to its own install via the body scope.
    res_a = _execute_with_app(e2e_client, headers, "workflows/main.py::main", app_a)
    res_b = _execute_with_app(e2e_client, headers, "workflows/main.py::main", app_b)
    assert res_a["status"] == "Success", res_a
    assert res_b["status"] == "Success", res_b
    assert res_a["result"] == {"marker": "orgbound"}, res_a
    assert res_b["result"] == {"marker": "bbb"}, res_b

    def _execute_with_header(app_id: str) -> dict:
        resp = e2e_client.post(
            "/api/workflows/execute",
            headers={**headers, "X-Bifrost-App": app_id},
            json={"workflow_id": "workflows/main.py::main", "sync": True},
        )
        assert resp.status_code == 200, f"execute failed: {resp.status_code} {resp.text}"
        return resp.json()

    # The deployed browser sends only X-Bifrost-App, with no body app_id.
    header_a = _execute_with_header(app_a)
    header_b = _execute_with_header(app_b)
    assert header_a["status"] == "Success", header_a
    assert header_b["status"] == "Success", header_b
    assert header_a["result"] == {"marker": "orgbound"}, header_a
    assert header_b["result"] == {"marker": "bbb"}, header_b


def test_workflow_404_includes_scope_diagnostics(
    e2e_client, platform_admin, orgbound_install,
):
    """A scope-resolution miss must identify itself: the 404 detail carries the
    ref and the derived install scope, so a dropped/wrong scope reads as
    `derived_solution_scope: null` instead of a mystery 404 (drive lesson —
    the unscoped courtesy fallback masked scope loss for a whole POC day)."""
    headers = platform_admin.headers
    app_a = orgbound_install["app_id"]

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
    e2e_client, platform_admin, orgbound_install,
):
    """Dev14 regression: an ORG-BOUND install's workflows carry the install's
    org; a platform admin whose effective org differs (the normal demo/support
    posture) must still resolve the install's own workflow by path::fn via the
    app header — superusers bypass the caller-org gate on the OWN-install
    match, exactly like resolve_solution_table_by_name does for tables.
    Global installs never caught this (their rows have organization_id NULL)."""
    headers = platform_admin.headers
    app_a = orgbound_install["app_id"]

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers={**headers, "X-Bifrost-App": app_a},
        json={"workflow_id": "workflows/main.py::main", "sync": True},
    )
    assert resp.status_code == 200, f"execute failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["status"] == "Success", body
    assert body["result"] == {"marker": "orgbound"}, body


def test_all_three_ref_shapes_resolve_identically(
    e2e_client, platform_admin, orgbound_install,
):
    """The calling contract: UUID, portable path::fn, AND bare workflow name
    must all resolve a deployed install's own workflow through the same
    header-scoped transport — including the hard case (ORG-BOUND install,
    admin caller in a different org)."""
    headers = platform_admin.headers
    app_id = orgbound_install["app_id"]
    wf_name = orgbound_install["workflow_name"]

    def _execute(ref: str) -> dict:
        resp = e2e_client.post(
            "/api/workflows/execute",
            headers={**headers, "X-Bifrost-App": app_id},
            json={"workflow_id": ref, "sync": True},
        )
        assert resp.status_code == 200, f"{ref!r} failed: {resp.status_code} {resp.text}"
        return resp.json()

    by_path = _execute("workflows/main.py::main")
    by_name = _execute(wf_name)
    by_uuid = _execute(by_path["workflow_id"])

    for label, res in (("path", by_path), ("name", by_name), ("uuid", by_uuid)):
        assert res["status"] == "Success", (label, res)
        assert res["result"] == {"marker": "orgbound"}, (label, res)
    assert by_path["workflow_id"] == by_name["workflow_id"] == by_uuid["workflow_id"]


def test_foreign_app_header_cannot_reach_other_orgs_workflow(
    e2e_client, orgbound_install, org2_user,
):
    """PINNING (expected to hold): a regular user from org2 smuggling org1's
    X-Bifrost-App must NOT execute org1's install workflow — the resolver's
    org gate (cascade scope) holds under ctx-first scoping."""
    app_a = orgbound_install["app_id"]

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers={**org2_user.headers, "X-Bifrost-App": app_a},
        json={"workflow_id": "workflows/main.py::main", "sync": True},
    )
    assert resp.status_code in (403, 404), (
        f"cross-org header execution must be refused, got {resp.status_code}: {resp.text}"
    )
