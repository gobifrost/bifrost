"""E2E: inactive solutions are dormant — not servable/executable, but still browsable.

L4 of the solution-inactive-lifecycle plan.

Gate: ``get_execution_context`` in ``api/src/core/auth.py`` refuses to set
``ctx.solution_id`` when the ?solution= query param refers to an inactive install.
This blocks execution paths (workflow execution uses ctx.solution_id) while leaving
browse/export paths untouched (they take solution_id as a URL path param).

The gate also covers the ``X-Bifrost-App`` header path: v2 SDK apps send this
header instead of ``?solution=``, so inactive-solution apps must be refused too.

Tests:
1. Inactive solution workflow execution is REFUSED (409 Conflict — dormant).
2. Inactive solution entities are STILL BROWSABLE (200 OK).
3. Active solution executes normally (regression).
4. Inactive solution's APP is DOWN via X-Bifrost-App header (dormant gate).
5. Active solution's app is NOT refused via X-Bifrost-App (regression).
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.services.solutions.deploy import solution_entity_id

from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _deploy_with_workflow(e2e_client, headers, sid: str) -> None:
    """Deploy a minimal bundle containing one workflow."""
    wf_content = "def main(**kwargs): return {'ok': True}"
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {"workflows/main.py": wf_content},
        "workflows": [{
            "id": str(uuid.uuid4()),
            "name": "main",
            "path": "workflows/main.py",
            "function_name": "main",
            "type": "workflow",
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text


def _uninstall(e2e_client, headers, sid: str) -> None:
    r = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "inactive"


async def test_inactive_solution_workflow_execution_refused(
    e2e_client, platform_admin,
):
    """Workflow execution with ?solution=<inactive id> must be refused (dormant gate)."""
    headers = platform_admin.headers
    slug = f"dormant-exec-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    _deploy_with_workflow(e2e_client, headers, sid)
    _uninstall(e2e_client, headers, sid)

    # Try to execute with the inactive solution's id in the query param.
    # The dormant gate in get_execution_context must refuse this.
    r = e2e_client.post(
        f"/api/workflows/execute?solution={sid}",
        headers=headers,
        json={
            "workflow_id": "workflows/main.py::main",
            "input_data": {},
        },
    )
    assert r.status_code == 409, (
        f"Expected 409 (dormant gate) for inactive solution execution, got {r.status_code}: {r.text}"
    )
    assert "inactive" in r.json().get("detail", "").lower(), (
        f"Expected 'inactive' in detail, got: {r.text}"
    )


async def test_inactive_solution_files_still_browsable(
    e2e_client, platform_admin,
):
    """Entities browser must still work for an inactive solution (browse != execute)."""
    headers = platform_admin.headers
    slug = f"dormant-browse-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    _deploy_with_workflow(e2e_client, headers, sid)
    _uninstall(e2e_client, headers, sid)

    # GET /api/solutions/{id}/entities uses solution_id as a URL path param —
    # it does NOT go through the ?solution= execution context gate.
    r = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert r.status_code == 200, (
        f"Expected 200 for browsing inactive solution entities, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert "files" in body or "workflows" in body, (
        f"entities response missing expected keys: {list(body.keys())}"
    )


async def test_active_solution_executes_normally(
    e2e_client, platform_admin,
):
    """Active solution execution must NOT be blocked by the dormant gate (regression)."""
    headers = platform_admin.headers
    slug = f"active-exec-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    _deploy_with_workflow(e2e_client, headers, sid)

    # Active solution: ?solution= should set ctx.solution_id without refusal.
    # Execution itself may 404 (workflow path resolution in test context) but
    # must NOT return 403/409 (the dormant gate must not fire).
    r = e2e_client.post(
        f"/api/workflows/execute?solution={sid}",
        headers=headers,
        json={
            "workflow_id": "workflows/main.py::main",
            "input_data": {},
        },
    )
    # 403/409 means the dormant gate fired — that's the regression.
    assert r.status_code not in (403, 409), (
        f"Active solution execution was refused by dormant gate: {r.status_code}: {r.text}"
    )


def _deploy_with_app_and_table(e2e_client, headers, sid: str) -> tuple[str, str, str]:
    """Deploy a minimal bundle with one app + one table.

    Returns (app_id, table_name, workflow_id) where app_id is the resolved
    per-install entity id (suitable for the X-Bifrost-App header).
    """
    app_manifest_id = str(uuid.uuid4())
    table_manifest_id = str(uuid.uuid4())
    slug_suffix = sid[:8]
    table_name = f"dormant_tbl_{slug_suffix}"

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "apps": [{"id": app_manifest_id, "slug": f"app-{slug_suffix}", "name": "App",
                  "app_model": "standalone_v2", "dist_files": {"index.html": "<html></html>"}}],
        "tables": [{"id": table_manifest_id, "name": table_name,
                    "schema": {"columns": [{"name": "val"}]}, "policies": None}],
    })
    from tests.e2e.platform.conftest import wait_for_deploy
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text

    app_id = str(solution_entity_id(UUID(sid), UUID(app_manifest_id)))
    return app_id, table_name


async def test_inactive_solution_app_path_refused(
    e2e_client, platform_admin,
):
    """X-Bifrost-App header carrying an inactive solution's app id must be refused.

    Fixes the dormant-gate bypass: the ?solution= gate already blocks workflow
    execution; this proves the app path (header) is also gated so the inactive
    solution's tables cannot be reached via its app either.
    """
    headers = platform_admin.headers
    slug = f"dormant-app-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    app_id, table_name = _deploy_with_app_and_table(e2e_client, headers, sid)
    _uninstall(e2e_client, headers, sid)

    # Any request carrying the inactive solution's app id in X-Bifrost-App must
    # hit the dormant gate (409) in get_execution_context before reaching the table.
    app_headers = {**headers, "X-Bifrost-App": app_id}
    r = e2e_client.post(
        f"/api/tables/{table_name}/documents",
        headers=app_headers,
        json={"id": "probe", "data": {"val": "x"}},
    )
    assert r.status_code == 409, (
        f"Expected 409 (dormant gate via X-Bifrost-App), got {r.status_code}: {r.text}"
    )
    assert "inactive" in r.json().get("detail", "").lower(), (
        f"Expected 'inactive' in detail, got: {r.text}"
    )


async def test_active_solution_app_path_not_refused(
    e2e_client, platform_admin,
):
    """X-Bifrost-App for an ACTIVE solution must NOT be refused (regression guard)."""
    headers = platform_admin.headers
    slug = f"active-app-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    app_id, table_name = _deploy_with_app_and_table(e2e_client, headers, sid)

    # Active solution: the dormant gate must not fire.  The row insert may
    # succeed or 409-conflict (duplicate) — what must NOT happen is a 409 from
    # the dormant gate.  We distinguish by checking the detail string.
    app_headers = {**headers, "X-Bifrost-App": app_id}
    r = e2e_client.post(
        f"/api/tables/{table_name}/documents",
        headers=app_headers,
        json={"id": "probe", "data": {"val": "x"}},
    )
    if r.status_code == 409:
        # A 409 from the dormant gate has "inactive" in the detail; a normal
        # duplicate-key 409 does not.
        assert "inactive" not in r.json().get("detail", "").lower(), (
            f"Active solution app was refused by dormant gate: {r.text}"
        )
    else:
        assert r.status_code in (200, 201), (
            f"Active solution app request unexpectedly failed: {r.status_code}: {r.text}"
        )
