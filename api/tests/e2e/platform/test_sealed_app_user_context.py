"""Authenticated app requests retain user authority inside sealed installs.

Seed metadata directly: these tests exercise request resolution and policy
enforcement, without an app build, Solution deployment, or worker job. Deferred
workflow requests prove admission and identity and are removed before due time.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete

from src.models.orm.applications import Application
from src.models.orm.executions import Execution
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.models.orm.tables import Document, Table
from src.models.orm.workflows import Workflow
from src.services.solutions.guard import install_solution_write_guard

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def sealed_app(db_session, org1, request):
    install_solution_write_guard()
    case = getattr(request, "param", {})
    org_id = None if case.get("global") else UUID(org1["id"])
    suffix = uuid4().hex[:10]
    solution = Solution(
        id=uuid4(), slug=f"sealed-{suffix}", name="Sealed App Fixture",
        organization_id=org_id, allow_inbound_access=False,
        allow_outbound_access=False,
    )
    app = Application(
        id=uuid4(), slug=f"sealed-app-{suffix}", name="Sealed App Fixture",
        organization_id=org_id, solution_id=solution.id,
        app_model="standalone_v2", access_level=case.get("app_access", "authenticated"),
    )
    workflow = Workflow(
        id=uuid4(), name=f"sealed_workflow_{suffix}",
        path=f"workflows/{suffix}.py", function_name="load",
        organization_id=org_id, solution_id=solution.id,
        access_level=case.get("workflow_access", "authenticated"), type="workflow", is_active=True,
    )
    shared_workflow = Workflow(
        id=uuid4(), name=f"shared_workflow_{suffix}", path=workflow.path,
        function_name="load", organization_id=org_id,
        access_level="authenticated", type="workflow", is_active=True,
    )
    table = Table(
        id=uuid4(), name=f"sealed_records_{suffix}",
        organization_id=org_id, solution_id=solution.id,
        access={"policies": [{"name": "read_only", "actions": ["read"]}]},
    )
    db_session.add(solution)
    await db_session.flush()
    db_session.add_all([app, workflow, shared_workflow, table])
    await db_session.flush()
    db_session.add_all([
        Document(id="record", table_id=table.id, data={"value": "visible"}),
        SolutionFileLocation(solution_id=solution.id, location="solutions"),
        FilePolicy(
            organization_id=org_id, solution_id=solution.id,
            location="solutions", path="",
            policies={"policies": [
                {"name": "read_only", "actions": ["read", "list"]},
                {"name": "fixture_setup", "actions": ["write", "delete"],
                 "when": {"user": "is_platform_admin"}},
            ]},
        ),
    ])
    await db_session.commit()
    workflow_ids, solution_id = [workflow.id, shared_workflow.id], solution.id
    try:
        yield SimpleNamespace(app=app, solution=solution, workflow=workflow, table=table)
    finally:
        await db_session.rollback()
        await db_session.execute(delete(Execution).where(Execution.workflow_id.in_(workflow_ids)))
        await db_session.execute(delete(Workflow).where(Workflow.id.in_(workflow_ids)))
        await db_session.execute(delete(Solution).where(Solution.id == solution_id))
        await db_session.commit()


def _schedule(client, headers, fixture, **extra):
    return client.post(
        "/api/workflows/execute", headers=headers,
        json={"workflow_id": f"{fixture.workflow.path}::load",
              "delay_seconds": 3600, **extra},
    )


@pytest.mark.parametrize("transport", ["header", "legacy_body", "body_with_solution"])
@pytest.mark.parametrize(
    "sealed_app", [pytest.param({}, id="org"), pytest.param({"global": True}, id="global")],
    indirect=True,
)
async def test_sealed_app_workflow_admission_preserves_user_identity(
    e2e_client, org1_user, db_session, sealed_app, transport,
):
    headers = org1_user.headers
    extra = {}
    if transport == "header":
        headers = {**headers, "X-Bifrost-App": str(sealed_app.app.id)}
    else:
        extra["app_id"] = str(sealed_app.app.id)
        if transport == "body_with_solution":
            extra["solution_id"] = str(sealed_app.solution.id)
    response = _schedule(e2e_client, headers, sealed_app, **extra)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "Scheduled"
    row = await db_session.get(Execution, UUID(response.json()["execution_id"]))
    assert row is not None
    assert row.workflow_id == sealed_app.workflow.id
    assert row.executed_by == org1_user.user_id
    assert row.run_user_id == org1_user.user_id


@pytest.mark.parametrize("sealed_app", [{"workflow_access": "role_based"}], indirect=True)
async def test_app_context_does_not_grant_workflow_sharing(
    e2e_client, org1_user, sealed_app,
):
    response = _schedule(
        e2e_client,
        {**org1_user.headers, "X-Bifrost-App": str(sealed_app.app.id)},
        sealed_app,
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("transport", ["header", "legacy_body"])
@pytest.mark.parametrize("sealed_app", [{"app_access": "role_based"}], indirect=True)
async def test_denied_app_context_cannot_fall_through_to_shared_workflow(
    e2e_client, org1_user, sealed_app, transport,
):
    headers = org1_user.headers
    extra = {}
    if transport == "header":
        headers = {**headers, "X-Bifrost-App": str(sealed_app.app.id)}
    else:
        extra["app_id"] = str(sealed_app.app.id)
    response = _schedule(e2e_client, headers, sealed_app, **extra)
    assert response.status_code == 404, response.text


async def test_foreign_user_cannot_select_app_install(
    e2e_client, org2_user, sealed_app,
):
    response = _schedule(
        e2e_client,
        {**org2_user.headers, "X-Bifrost-App": str(sealed_app.app.id)},
        sealed_app,
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("identifier", ["name", "uuid"])
async def test_sealed_app_table_resolution_retains_row_policy(
    e2e_client, org1_user, sealed_app, identifier,
):
    headers = {**org1_user.headers, "X-Bifrost-App": str(sealed_app.app.id)}
    table_ref = sealed_app.table.name if identifier == "name" else str(sealed_app.table.id)
    response = e2e_client.get(
        f"/api/tables/{table_ref}/documents/record", headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"value": "visible"}
    denied = e2e_client.post(
        f"/api/tables/{table_ref}/documents", headers=headers,
        json={"id": "blocked", "data": {"value": "blocked"}},
    )
    assert denied.status_code == 403, denied.text


async def test_sealed_app_file_read_retains_file_policy(
    e2e_client, platform_admin, org1_user, sealed_app,
):
    admin_headers = {**platform_admin.headers, "X-Bifrost-App": str(sealed_app.app.id)}
    body = {"path": "fixture.txt", "location": "solutions", "mode": "cloud"}
    created = e2e_client.post(
        "/api/files/write", headers=admin_headers,
        json={**body, "content": "visible"},
    )
    assert created.status_code == 204, created.text
    try:
        headers = {**org1_user.headers, "X-Bifrost-App": str(sealed_app.app.id)}
        response = e2e_client.post("/api/files/read", headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["content"] == "visible"
        denied = e2e_client.post(
            "/api/files/write", headers=headers, json={**body, "content": "blocked"},
        )
        assert denied.status_code == 403, denied.text
    finally:
        removed = e2e_client.post("/api/files/delete", headers=admin_headers, json=body)
        assert removed.status_code == 204, removed.text


@pytest.mark.parametrize(
    "sealed_app", [pytest.param({}, id="org"), pytest.param({"global": True}, id="global")],
    indirect=True,
)
async def test_user_can_load_app_manifest_and_dist_without_source_permission(
    e2e_client, org1_user, sealed_app,
):
    """Opening an app needs the User base role, not source-reading grants.

    Seed static dist directly: no deploy, install, build, or worker jobs.
    """
    from src.services.solutions.app_build import SolutionAppBuilder

    builder = SolutionAppBuilder()
    app_id = sealed_app.app.id
    await builder._upload_dist(app_id, {
        "index.html": b'<script type="module" src="assets/main.js"></script>',
        "assets/main.js": b'export const loaded = true;',
    })
    try:
        response = e2e_client.get(
            f"/api/applications/{app_id}/bundle-manifest?mode=live",
            headers=org1_user.headers,
        )
        assert response.status_code == 200, response.text
        manifest = response.json()
        assert manifest["entry"] == "assets/main.js"
        asset = e2e_client.get(
            f'{manifest["base_url"]}/{manifest["entry"]}', headers=org1_user.headers,
        )
        assert asset.status_code == 200, asset.text
        assert asset.text == 'export const loaded = true;'
        source = e2e_client.get(
            f"/api/applications/{app_id}/files", headers=org1_user.headers,
        )
        assert source.status_code == 403, source.text
    finally:
        await builder.delete_dist(app_id)


@pytest.mark.parametrize("sealed_app", [{"app_access": "role_based"}], indirect=True)
async def test_bundle_manifest_preserves_app_sharing(e2e_client, org1_user, sealed_app):
    response = e2e_client.get(
        f"/api/applications/{sealed_app.app.id}/bundle-manifest?mode=live",
        headers=org1_user.headers,
    )
    assert response.status_code == 404, response.text


async def test_bundle_manifest_preserves_organization_reach(e2e_client, org2_user, sealed_app):
    response = e2e_client.get(
        f"/api/applications/{sealed_app.app.id}/bundle-manifest?mode=live",
        headers=org2_user.headers,
    )
    assert response.status_code == 404, response.text
