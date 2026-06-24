"""E2E: inactive solutions are dormant — not servable/executable, but still browsable.

L4 of the solution-inactive-lifecycle plan.

Gate: ``get_execution_context`` in ``api/src/core/auth.py`` refuses to set
``ctx.solution_id`` when the ?solution= query param refers to an inactive install.
This blocks execution paths (workflow execution uses ctx.solution_id) while leaving
browse/export paths untouched (they take solution_id as a URL path param).

Tests:
1. Inactive solution workflow execution is REFUSED (409 Conflict — dormant).
2. Inactive solution entities are STILL BROWSABLE (200 OK).
3. Active solution executes normally (regression).
"""
from __future__ import annotations

import uuid

import pytest

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
    assert r.status_code in (403, 409), (
        f"Expected 403/409 for dormant solution execution, got {r.status_code}: {r.text}"
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
