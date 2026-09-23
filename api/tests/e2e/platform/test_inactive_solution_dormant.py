"""E2E: inactive solutions are dormant — not servable/executable, but still browsable.

L4 of the solution-inactive-lifecycle plan.

Gate 1 (HTTP): ``get_execution_context`` in ``api/src/core/auth.py`` refuses to set
``ctx.solution_id`` when the ?solution= query param refers to an inactive install.
This blocks execution paths (workflow execution uses ctx.solution_id) while leaving
browse/export paths untouched (they take solution_id as a URL path param).

The gate also covers the ``X-Bifrost-App`` header path: v2 SDK apps send this
header instead of ``?solution=``, so inactive-solution apps must be refused too.

Gate 2 (worker-side): ``get_workflow_for_execution`` in
``services/execution/service.py`` applies the same inactive-solution filter at the
DB query level.  This covers scheduled, event-triggered, API-key, and worker-queue
paths that do NOT go through ``get_execution_context``.  The same gate is mirrored
for ``POST /api/agent-runs/execute`` in ``routers/agent_runs.py``.

The live lifecycle checks active and inactive workflow/app access plus inactive
entity browsing on one deployed install. DB-backed worker-gate tests below check
inactive, active, and _repo workflow resolution separately.
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


def _deploy_with_workflow_app_and_table(e2e_client, headers, sid: str) -> tuple[str, str]:
    """Deploy one bundle for both execution-context transports."""
    app_manifest_id = str(uuid.uuid4())
    table_manifest_id = str(uuid.uuid4())
    table_name = f"dormant_tbl_{sid[:8]}"
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {"workflows/main.py": "def main(**kwargs): return {'ok': True}"},
        "workflows": [{
            "id": str(uuid.uuid4()),
            "name": "main", "path": "workflows/main.py",
            "function_name": "main", "type": "workflow",
        }],
        "apps": [{
            "id": app_manifest_id, "slug": f"app-{sid[:8]}", "name": "App",
            "app_model": "standalone_v2",
            "dist_files": {"index.html": "<html></html>"},
        }],
        "tables": [{
            "id": table_manifest_id, "name": table_name,
            "schema": {"columns": [{"name": "val"}]}, "policies": None,
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text
    app_id = str(solution_entity_id(UUID(sid), UUID(app_manifest_id)))
    return app_id, table_name


def _uninstall(e2e_client, headers, sid: str) -> None:
    r = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "inactive"


async def test_solution_dormant_gate_for_workflow_app_and_browser(
    e2e_client, platform_admin,
):
    """The same install is executable while active and dormant after uninstall."""
    headers = platform_admin.headers
    slug = f"dormant-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    app_id, table_name = _deploy_with_workflow_app_and_table(e2e_client, headers, sid)
    workflow_body = {
        "workflow_id": "workflows/main.py::main",
        "input_data": {},
    }
    app_headers = {**headers, "X-Bifrost-App": app_id}
    app_url = f"/api/tables/{table_name}/documents"

    # Both execution-context transports accept an active install.
    active_workflow = e2e_client.post(
        f"/api/workflows/execute?solution={sid}", headers=headers, json=workflow_body
    )
    assert active_workflow.status_code not in (403, 409), active_workflow.text
    active_app = e2e_client.post(
        app_url, headers=app_headers, json={"id": "probe", "data": {"val": "x"}}
    )
    assert active_app.status_code in (200, 201), active_app.text

    _uninstall(e2e_client, headers, sid)

    inactive_workflow = e2e_client.post(
        f"/api/workflows/execute?solution={sid}", headers=headers, json=workflow_body
    )
    assert inactive_workflow.status_code == 409, inactive_workflow.text
    assert "inactive" in inactive_workflow.json().get("detail", "").lower()
    inactive_app = e2e_client.post(
        app_url, headers=app_headers, json={"id": "probe-2", "data": {"val": "x"}}
    )
    assert inactive_app.status_code == 409, inactive_app.text
    assert "inactive" in inactive_app.json().get("detail", "").lower()

    # Entity browsing uses a path id and remains available after uninstall.
    browser = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert browser.status_code == 200, browser.text
    assert "files" in browser.json() or "workflows" in browser.json()


# ---------------------------------------------------------------------------
# Worker-side gate tests (Gate 2): get_workflow_for_execution
# ---------------------------------------------------------------------------

class TestWorkerSideInactiveSolutionGate:
    """Verify that get_workflow_for_execution refuses workflows belonging to
    an inactive solution, covering the scheduled/event/API-key/worker-queue
    paths that bypass get_execution_context (Gate 1).
    """

    async def test_inactive_solution_workflow_not_resolved(self, db_session):
        """get_workflow_for_execution raises WorkflowNotFoundError for an inactive solution's workflow."""
        from uuid import uuid4
        from src.models.orm.organizations import Organization
        from src.models.orm.solutions import Solution
        from src.models.orm.workflows import Workflow
        from src.services.execution.service import (
            WorkflowNotFoundError,
            get_workflow_for_execution,
        )

        db = db_session
        org = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
        db.add(org)
        await db.flush()

        sol = Solution(
            id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S",
            organization_id=org.id, allow_outbound_access=False, status="inactive",
        )
        db.add(sol)
        await db.flush()

        wf = Workflow(
            id=uuid4(), name="w", function_name="main", path="workflows/w.py",
            type="workflow", is_active=True, organization_id=org.id, solution_id=sol.id,
        )
        db.add(wf)
        await db.flush()

        with pytest.raises(WorkflowNotFoundError):
            await get_workflow_for_execution(str(wf.id), db=db)

    async def test_active_solution_workflow_resolves(self, db_session):
        """get_workflow_for_execution resolves normally for an active solution's workflow (regression)."""
        from uuid import uuid4
        from src.models.orm.organizations import Organization
        from src.models.orm.solutions import Solution
        from src.models.orm.workflows import Workflow
        from src.services.execution.service import get_workflow_for_execution

        db = db_session
        org = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
        db.add(org)
        await db.flush()

        sol = Solution(
            id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S",
            organization_id=org.id, allow_outbound_access=False, status="active",
        )
        db.add(sol)
        await db.flush()

        wf = Workflow(
            id=uuid4(), name="w", function_name="main", path="workflows/w.py",
            type="workflow", is_active=True, organization_id=org.id, solution_id=sol.id,
        )
        db.add(wf)
        await db.flush()

        data = await get_workflow_for_execution(str(wf.id), db=db)
        assert data["solution_id"] == str(sol.id)

    async def test_repo_workflow_resolves(self, db_session):
        """get_workflow_for_execution resolves normally for a _repo workflow (no solution, regression)."""
        from uuid import uuid4
        from src.models.orm.workflows import Workflow
        from src.services.execution.service import get_workflow_for_execution

        db = db_session
        wf = Workflow(
            id=uuid4(), name="w", function_name="main", path="workflows/w.py",
            type="workflow", is_active=True, organization_id=None, solution_id=None,
        )
        db.add(wf)
        await db.flush()

        data = await get_workflow_for_execution(str(wf.id), db=db)
        assert data["solution_id"] is None
        assert data["can_access_global_repo"] is False
