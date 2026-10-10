"""E2E: workflow execute/cancel through the shared mutation service.

Exercises ``POST /api/workflows/execute`` and
``POST /api/workflows/executions/{execution_id}/cancel`` end to end via
the thinned HTTP handlers (which delegate to
``shared.sdk_workflow_execution``):

- normal async enqueue (Pending + committed row visible via GET),
- scheduled enqueue + cancel + second-cancel 409,
- Solution inbound denial (unknown install UUID -> 404),
- cross-org execute denial (org2 user on an org1 workflow -> 404),
- run_as: refused without Impersonate Users (403), unknown user (404), and
  an Impersonate Users grant at Contoso allowing a Contoso user but not a
  Fabrikam one, each audited as an enforced ``run_as`` check,
- inline-code non-admin 403 and unknown-workflow 404 detail,
- cancel 404/403 precedence.
"""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import pytest_asyncio

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from tests.e2e.conftest import write_and_register
from tests.e2e.fixtures.run_as import RUN_AS_DENIED, create_person, ok, recorded_run_as


pytestmark = pytest.mark.e2e


WORKFLOW_CONTENT = '''"""E2E Workflow Mutations Service Workflow"""
from bifrost import workflow

@workflow(
    name="{name}",
    description="Workflow used by workflow-mutations-service E2E tests",
)
async def {func}(foo: str = "bar") -> dict:
    return {{"ok": True, "foo": foo}}
'''


@pytest.fixture(scope="module")
def mutation_workflow(e2e_client, platform_admin):
    """Global workflow for enqueue/scheduling/denial tests."""
    func = "e2e_wf_mutations_main"
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_wf_mutations_main.py",
        WORKFLOW_CONTENT.format(name="e2e_wf_mutations_main", func=func),
        func,
        organization_id=None,  # global
    )
    yield {"id": result["id"], "name": result.get("name", func)}

    e2e_client.delete(
        "/api/files/editor?path=e2e_wf_mutations_main.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def org1_scoped_workflow(e2e_client, platform_admin, org1):
    """Org1-scoped workflow for the cross-org denial test."""
    func = "e2e_wf_mutations_org1"
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_wf_mutations_org1.py",
        WORKFLOW_CONTENT.format(name="e2e_wf_mutations_org1", func=func),
        func,
        organization_id=org1["id"],
    )
    yield {"id": result["id"], "name": result.get("name", func)}

    e2e_client.delete(
        "/api/files/editor?path=e2e_wf_mutations_org1.py",
        headers=platform_admin.headers,
    )


@pytest_asyncio.fixture
async def cleanup_execution_rows(db_session: AsyncSession):  # type: ignore[misc]
    """Remove Execution rows created by each test."""
    created_ids: list[UUID] = []
    yield created_ids
    if created_ids:
        await db_session.execute(
            delete(Execution).where(Execution.id.in_(created_ids))
        )
        await db_session.commit()


def _schedule(
    e2e_client, headers, workflow_id, cleanup_execution_rows, **kwargs
) -> UUID:
    body = {"workflow_id": workflow_id, "input_data": {}, "delay_seconds": 300}
    body.update(kwargs)
    resp = e2e_client.post(
        "/api/workflows/execute", headers=headers, json=body
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "Scheduled", data
    exec_id = UUID(data["execution_id"])
    cleanup_execution_rows.append(exec_id)
    return exec_id


def test_normal_enqueue_returns_pending_and_commits_row(
    e2e_client, platform_admin, mutation_workflow, cleanup_execution_rows
):
    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={"workflow_id": mutation_workflow["id"], "input_data": {}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Pending", body
    exec_id = UUID(body["execution_id"])
    cleanup_execution_rows.append(exec_id)

    # The enqueue committed: the row is visible to the submitter.
    got = e2e_client.get(
        f"/api/executions/{exec_id}", headers=platform_admin.headers
    )
    assert got.status_code == 200, got.text
    assert got.json()["execution_id"] == str(exec_id)


def test_scheduled_enqueue_and_cancel(
    e2e_client, platform_admin, mutation_workflow, cleanup_execution_rows
):
    exec_id = _schedule(
        e2e_client,
        platform_admin.headers,
        mutation_workflow["id"],
        cleanup_execution_rows,
    )

    cancel = e2e_client.post(
        f"/api/workflows/executions/{exec_id}/cancel",
        headers=platform_admin.headers,
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json() == {
        "execution_id": str(exec_id),
        "status": ExecutionStatus.CANCELLED.value,
    }

    again = e2e_client.post(
        f"/api/workflows/executions/{exec_id}/cancel",
        headers=platform_admin.headers,
    )
    assert again.status_code == 409, again.text
    assert "Cancelled" in again.json()["detail"]


def test_solution_inbound_denied_is_404(
    e2e_client, platform_admin, mutation_workflow
):
    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={
            "workflow_id": mutation_workflow["id"],
            "input_data": {},
            "solution_id": str(uuid4()),
        },
    )
    assert resp.status_code == 404, resp.text
    assert "not found" in resp.json()["detail"]["message"]


def test_cross_org_execute_is_404(
    e2e_client, org2_user, org1_scoped_workflow
):
    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=org2_user.headers,
        json={"workflow_id": org1_scoped_workflow["id"], "input_data": {}},
    )
    assert resp.status_code == 404, resp.text


def test_run_as_without_impersonate_users_is_403(
    e2e_client, platform_admin, org1_user, non_admin_user, mutation_workflow
):
    # The run_as check sits after workflow resolution, so open the fixture
    # workflow to authenticated callers first (admin paths are unaffected).
    patch_resp = e2e_client.patch(
        f"/api/workflows/{mutation_workflow['id']}",
        headers=platform_admin.headers,
        json={"access_level": "authenticated"},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=org1_user.headers,
        json={
            "workflow_id": mutation_workflow["id"],
            "input_data": {},
            "run_as": str(non_admin_user.user_id),
        },
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail == RUN_AS_DENIED


def test_run_as_unknown_user_is_refused_like_one_out_of_reach(
    e2e_client, platform_admin, mutation_workflow
):
    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={
            "workflow_id": mutation_workflow["id"],
            "input_data": {},
            "run_as": str(uuid4()),
        },
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail == RUN_AS_DENIED


@pytest.fixture(scope="module")
def impersonation_world(e2e_client, platform_admin):
    """A Contoso person holding Impersonate Users at Contoso, a Contoso
    colleague, a Fabrikam person, and a Contoso workflow they can all run."""
    tag = uuid4().hex[:8]
    admin = platform_admin.headers
    contoso = ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Contoso-{tag}"}), 201)
    fabrikam = ok(e2e_client.post("/api/organizations", headers=admin, json={"name": f"Fabrikam-{tag}"}), 201)
    role = ok(e2e_client.post("/api/roles", headers=admin, json={"name": f"Contoso Impersonation {tag}"}), 201)
    ok(
        e2e_client.put(
            f"/api/roles/{role['id']}/permissions",
            headers=admin,
            json={"permissions": ["users.impersonate"]},
        )
    )
    initiator = create_person(
        e2e_client,
        admin,
        contoso,
        tag,
        "initiator",
        additional=[{"role_id": role["id"], "boundaries": [{"kind": "organization", "organization_id": contoso["id"]}]}],
    )
    colleague = create_person(e2e_client, admin, contoso, tag, "colleague", additional=[])
    outsider = create_person(e2e_client, admin, fabrikam, tag, "outsider", additional=[])
    func = f"e2e_wf_run_as_{tag}"
    path = f"{func}.py"
    workflow = write_and_register(
        e2e_client,
        admin,
        path,
        WORKFLOW_CONTENT.format(name=func, func=func),
        func,
        organization_id=contoso["id"],
    )
    ok(e2e_client.patch(f"/api/workflows/{workflow['id']}", headers=admin, json={"access_level": "authenticated"}))

    yield {
        "initiator": initiator,
        "colleague": colleague,
        "outsider": outsider,
        "workflow": workflow,
        "contoso": contoso,
    }

    e2e_client.delete(f"/api/files/editor?path={path}", headers=admin)
    for person in (initiator, colleague, outsider):
        e2e_client.delete(f"/api/users/{person.user_id}", headers=admin)
    e2e_client.delete(f"/api/roles/{role['id']}", headers=admin)
    e2e_client.delete(f"/api/organizations/{contoso['id']}", headers=admin)
    e2e_client.delete(f"/api/organizations/{fabrikam['id']}", headers=admin)


def test_impersonate_users_runs_as_a_user_in_its_organization(
    e2e_client, impersonation_world, cleanup_execution_rows, async_session_factory
):
    world = impersonation_world
    initiator, colleague = world["initiator"], world["colleague"]
    exec_id = _schedule(
        e2e_client,
        initiator.headers,
        world["workflow"]["id"],
        cleanup_execution_rows,
        run_as=str(colleague.user_id),
    )

    async def identities():
        from src.core.database import close_db

        try:
            async with async_session_factory() as session:
                return (
                    await session.execute(
                        select(Execution.executed_by, Execution.run_user_id, Execution.started_by_user_id).where(
                            Execution.id == exec_id
                        )
                    )
                ).one()
        finally:
            await close_db()

    acting, run_user, started_by = asyncio.run(identities())
    assert (acting, run_user, started_by) == (colleague.user_id, initiator.user_id, initiator.user_id)

    checks = recorded_run_as(async_session_factory, initiator.user_id, colleague.user_id)
    summary = [(c["outcome"], str(c["organization_id"]), c["details"]["enforced"]) for c in checks]
    assert summary == [("success", world["contoso"]["id"], True)]


def test_impersonate_users_is_refused_outside_its_organization(
    e2e_client, impersonation_world, async_session_factory
):
    world = impersonation_world
    initiator, outsider = world["initiator"], world["outsider"]

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=initiator.headers,
        json={"workflow_id": world["workflow"]["id"], "input_data": {}, "run_as": str(outsider.user_id)},
    )
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail == RUN_AS_DENIED

    checks = recorded_run_as(async_session_factory, initiator.user_id, outsider.user_id)
    summary = [(c["outcome"], c["details"]["enforced"]) for c in checks]
    assert summary == [("failure", True)]


def test_the_initiator_cancels_a_run_scheduled_as_a_colleague(
    e2e_client, impersonation_world, cleanup_execution_rows
):
    world = impersonation_world
    exec_id = _schedule(
        e2e_client,
        world["initiator"].headers,
        world["workflow"]["id"],
        cleanup_execution_rows,
        run_as=str(world["colleague"].user_id),
    )

    by_outsider = e2e_client.post(f"/api/workflows/executions/{exec_id}/cancel", headers=world["outsider"].headers)
    by_initiator = e2e_client.post(f"/api/workflows/executions/{exec_id}/cancel", headers=world["initiator"].headers)

    assert by_outsider.status_code == 403, by_outsider.text
    cancelled = ok(by_initiator)
    assert cancelled == {"execution_id": str(exec_id), "status": ExecutionStatus.CANCELLED.value}


def test_inline_code_non_admin_is_403(e2e_client, org1_user):
    import base64

    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=org1_user.headers,
        json={
            "code": base64.b64encode(b"return {'ok': True}").decode(),
            "input_data": {},
        },
    )
    assert resp.status_code == 403, resp.text


def test_unknown_workflow_404_carries_ref_detail(
    e2e_client, platform_admin
):
    missing = str(uuid4())
    resp = e2e_client.post(
        "/api/workflows/execute",
        headers=platform_admin.headers,
        json={"workflow_id": missing, "input_data": {}},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["workflow_ref"] == missing


def test_cancel_not_found_is_404(e2e_client, platform_admin):
    resp = e2e_client.post(
        f"/api/workflows/executions/{uuid4()}/cancel",
        headers=platform_admin.headers,
    )
    assert resp.status_code == 404, resp.text


def test_cancel_by_non_submitter_is_403(
    e2e_client, platform_admin, org1_user, mutation_workflow,
    cleanup_execution_rows,
):
    exec_id = _schedule(
        e2e_client,
        platform_admin.headers,
        mutation_workflow["id"],
        cleanup_execution_rows,
    )
    resp = e2e_client.post(
        f"/api/workflows/executions/{exec_id}/cancel",
        headers=org1_user.headers,
    )
    assert resp.status_code == 403, resp.text
