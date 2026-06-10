"""execute_form resolves form.workflow_id with the form's install scope:
a solution form reaches its OWN workflow (path::fn ref), a _repo form the _repo one.

Also pins the RBAC contract: the handler resolves the workflow on the FORM's
behalf (is_superuser=True), so a form user with no role on a ``role_based``
workflow must still reach it — the form's own access gate is authoritative.
"""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.forms import Form
from src.models.orm.workflows import Workflow
from src.repositories.workflows import WorkflowRepository


async def _org(db):
    o = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
    db.add(o)
    await db.flush()
    return o


@pytest.mark.e2e
class TestFormExecuteSolutionScope:
    async def test_solution_form_resolves_own_workflow_by_pathref(self, db_session):
        db = db_session
        org = (await _org(db)).id
        sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=org)
        db.add(sol)
        await db.flush()
        repo_wf = Workflow(id=uuid4(), name="repo", function_name="main", path="workflows/foo.py",
                           type="workflow", is_active=True, organization_id=None, solution_id=None)
        own_wf = Workflow(id=uuid4(), name="own", function_name="main", path="workflows/foo.py",
                          type="workflow", is_active=True, organization_id=org, solution_id=sol.id)
        db.add_all([repo_wf, own_wf])
        await db.flush()
        form = Form(id=uuid4(), name="f", organization_id=org, solution_id=sol.id,
                    workflow_id="workflows/foo.py::main", created_by="test")
        db.add(form)
        await db.flush()

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        resolved = await repo.resolve(form.workflow_id, solution_scope=form.solution_id)
        assert resolved is not None
        assert resolved.id == own_wf.id, "solution form must resolve its install's workflow"

    async def test_repo_form_resolves_repo_workflow(self, db_session):
        db = db_session
        org = (await _org(db)).id
        repo_wf = Workflow(id=uuid4(), name="repo", function_name="main", path="workflows/bar.py",
                           type="workflow", is_active=True, organization_id=None, solution_id=None)
        db.add(repo_wf)
        await db.flush()
        form = Form(id=uuid4(), name="f", organization_id=org, solution_id=None,
                    workflow_id="workflows/bar.py::main", created_by="test")
        db.add(form)
        await db.flush()

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        resolved = await repo.resolve(form.workflow_id, solution_scope=form.solution_id)
        assert resolved is not None and resolved.id == repo_wf.id

    async def test_uuid_workflow_id_still_resolves(self, db_session):
        # A non-solution form whose workflow_id is a plain UUID still resolves.
        db = db_session
        org = (await _org(db)).id
        wf = Workflow(id=uuid4(), name="w", function_name="main", path="workflows/w.py",
                      type="workflow", is_active=True, organization_id=None, solution_id=None)
        db.add(wf)
        await db.flush()
        form = Form(id=uuid4(), name="f", organization_id=org, solution_id=None,
                    workflow_id=str(wf.id), created_by="test")
        db.add(form)
        await db.flush()

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        resolved = await repo.resolve(form.workflow_id, solution_scope=form.solution_id)
        assert resolved is not None and resolved.id == wf.id

    async def test_handler_repo_bypasses_workflow_rbac_filter(self, db_session):
        """The handler resolves on the FORM's behalf (is_superuser=True), so a
        form user with no role on a ``role_based`` workflow STILL reaches it.

        Pins the RBAC regression: a user-scoped repo (is_superuser=False) would
        404 the same workflow because no WorkflowRole grants the user access —
        which is exactly why the handler must NOT use the user's privileges.
        """
        db = db_session
        org = (await _org(db)).id
        user_id = uuid4()
        # role_based workflow with NO WorkflowRole rows -> no user can directly access it.
        wf = Workflow(id=uuid4(), name="locked", function_name="main", path="workflows/locked.py",
                      type="workflow", is_active=True, organization_id=org, solution_id=None,
                      access_level="role_based")
        db.add(wf)
        await db.flush()
        form = Form(id=uuid4(), name="f", organization_id=org, solution_id=None,
                    workflow_id=str(wf.id), created_by="test")
        db.add(form)
        await db.flush()

        # What the handler does now: resolve on the form's behalf.
        handler_repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        resolved = await handler_repo.resolve(form.workflow_id, solution_scope=form.solution_id)
        assert resolved is not None and resolved.id == wf.id, (
            "form must reach its role_based workflow regardless of the user's roles"
        )

        # What the buggy version did: resolve with the user's privileges -> 404.
        user_repo = WorkflowRepository(db, org_id=org, user_id=user_id, is_superuser=False)
        denied = await user_repo.resolve(form.workflow_id, solution_scope=form.solution_id)
        assert denied is None, (
            "user-scoped resolve applies the workflow RBAC filter — proving why the "
            "handler must resolve as the form (is_superuser=True), not the user"
        )


@pytest.mark.e2e
class TestFormExecuteEndpointRbac:
    """Drive the real POST /api/forms/{id}/execute endpoint as a NON-superuser
    with form access but NO role on a role_based workflow. Against the pre-fix
    code (resolver constructed with the user's privileges) this 404s; with the
    fix it executes."""

    def _register_workflow(self, e2e_client, admin, path, func) -> str:
        content = (
            "from bifrost import workflow\n\n"
            f"@workflow(name='{func}')\n"
            f"def {func}(message: str = 'hi'):\n"
            "    return {'echo': message}\n"
        )
        w = e2e_client.put(
            "/api/files/editor/content",
            headers=admin.headers,
            json={"path": path, "content": content, "encoding": "utf-8"},
        )
        assert w.status_code in (200, 201), w.text
        r = e2e_client.post(
            "/api/workflows/register",
            headers=admin.headers,
            json={"path": path, "function_name": func, "access_level": "role_based"},
        )
        assert r.status_code in (200, 201), r.text
        return r.json()["id"]

    def test_non_superuser_form_user_executes_role_based_workflow(
        self, e2e_client, platform_admin, org1, org1_user
    ):
        path = f"workflows/e2e_form_rbac_{uuid4().hex[:8]}.py"
        func = f"e2e_form_rbac_{uuid4().hex[:8]}"
        wf_id = self._register_workflow(e2e_client, platform_admin, path, func)

        # Role gates FORM access; the user is assigned to it. The workflow has
        # NO role assignment, so org1_user has no direct role on the workflow.
        role = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": f"FormRBAC-{uuid4().hex[:6]}", "description": "form gate"},
        )
        assert role.status_code == 201, role.text
        role_id = role.json()["id"]

        form = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "RBAC Execute Form",
                "workflow_id": wf_id,
                "form_schema": {"fields": [{"name": "message", "type": "text", "label": "M"}]},
                "access_level": "role_based",
                "organization_id": org1["id"],
            },
        )
        assert form.status_code == 201, form.text
        form_id = form.json()["id"]

        # Assign form to role, and the user to the role -> form gate passes.
        assert e2e_client.post(
            f"/api/roles/{role_id}/forms",
            headers=platform_admin.headers,
            json={"form_ids": [form_id]},
        ).status_code in (200, 201, 204)
        assert e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(org1_user.user_id)]},
        ).status_code in (200, 201, 204)

        try:
            # Execute as the NON-superuser form user. The user has form access
            # but no role on the workflow. Must NOT be 403 (form gate) or 404
            # (workflow RBAC filter) — the regression manifested as a 404 here.
            r = e2e_client.post(
                f"/api/forms/{form_id}/execute",
                headers=org1_user.headers,
                json={"form_data": {"message": "hello"}},
            )
            assert r.status_code not in (403, 404), (
                f"role_based workflow unreachable through the form for a non-superuser "
                f"user with form access: {r.status_code} {r.text}"
            )
            assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
            assert r.json().get("execution_id"), r.text
        finally:
            e2e_client.delete(f"/api/forms/{form_id}", headers=platform_admin.headers)
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)
            e2e_client.delete(f"/api/files/editor?path={path}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestCrossOrgFormExecuteAnchor:
    """Resolution scope and execution org come from the ANCHOR entity (the form).

    A platform admin (home org != B) executing org B's solution-deployed form
    must run the install's OWN workflow — not a global _repo/ decoy at the same
    path::fn — and the execution row must be stamped with org B (the form's
    data world), not the caller's org.

    Pre-fix, the WorkflowRepository cascade was anchored to ctx.org_id (the
    CALLER's org), so the install's org-B workflow row was filtered out of the
    candidate set before the solution_scope disambiguation ran, silently
    resolving the global decoy; the execution was also stamped with the
    caller's org.
    """

    async def test_admin_cross_org_form_execute_resolves_install_workflow(
        self, db_session, e2e_client, platform_admin
    ):
        db = db_session
        org_b = (await _org(db)).id
        sol = Solution(
            id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=org_b
        )
        db.add(sol)
        await db.flush()

        path = f"workflows/xorg_anchor_{uuid4().hex[:8]}.py"
        # The decoy: a global _repo/ workflow at the SAME path::fn. The buggy
        # caller-anchored cascade (admin home org is not B) only sees
        # (caller org OR global) rows, so it silently resolves this one.
        decoy_wf = Workflow(
            id=uuid4(), name="decoy", function_name="main", path=path,
            type="workflow", is_active=True, organization_id=None, solution_id=None,
        )
        own_wf = Workflow(
            id=uuid4(), name="own", function_name="main", path=path,
            type="workflow", is_active=True, organization_id=org_b, solution_id=sol.id,
        )
        db.add_all([decoy_wf, own_wf])
        await db.flush()
        form = Form(
            id=uuid4(), name="xorg-anchor-form", organization_id=org_b,
            solution_id=sol.id, workflow_id=f"{path}::main", created_by="test",
        )
        db.add(form)
        # The API process must see these rows. No cleanup: the entities are
        # solution-managed (read-only — neither the API nor the ORM guard
        # allows deleting them) and uniquely named, so leftovers are inert;
        # the test stack resets state before each run.
        await db.commit()

        # Scheduled execute: the SCHEDULED row is inserted synchronously by the
        # handler itself (no worker round-trip), so both the resolution anchor
        # AND the execution's organization_id are observable deterministically.
        r = e2e_client.post(
            f"/api/forms/{form.id}/execute",
            headers=platform_admin.headers,
            json={"form_data": {}, "delay_seconds": 3600},
        )
        assert r.status_code == 200, (
            f"cross-org form execute failed: {r.status_code} {r.text}"
        )
        body = r.json()
        assert body["workflow_id"] == str(own_wf.id), (
            "cross-org caller resolved the wrong workflow (the global _repo/ "
            f"decoy, not the install's own): got {body['workflow_id']}, "
            f"expected {own_wf.id}"
        )

        # Lock the cross-org premise the decoy relies on: the session admin's
        # home org must differ from the freshly created org B.
        from src.models.orm.users import User

        admin_org = (
            await db.execute(
                select(User.organization_id).where(User.email == platform_admin.email)
            )
        ).scalar_one()
        assert admin_org != org_b, "fixture admin unexpectedly homed in org B"

        exec_row = (
            await db.execute(
                select(Execution).where(Execution.id == UUID(body["execution_id"]))
            )
        ).scalar_one()
        # form_id propagation is the one thing the API response can't prove;
        # workflow_id is already asserted via the response body above.
        assert exec_row.form_id == form.id
        assert exec_row.organization_id == org_b, (
            f"execution stamped with caller org {exec_row.organization_id}, "
            f"expected the form's org {org_b}"
        )

    async def test_admin_cross_org_startup_resolves_install_launch_workflow(
        self, db_session, e2e_client, platform_admin
    ):
        """The LAUNCH-workflow path must anchor to the form's org like execute.

        Pre-fix, the caller-org cascade excluded org B's install-owned launch
        workflow, so a cross-org admin's startup call 404'd with "Launch
        workflow not found" even though the install's row exists. Post-fix the
        ref resolves; the run may then fail later for unrelated reasons (no
        module source seeded), but it must get PAST resolution.
        """
        db = db_session
        org_b = (await _org(db)).id
        sol = Solution(
            id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=org_b
        )
        db.add(sol)
        await db.flush()

        path = f"workflows/xorg_launch_{uuid4().hex[:8]}.py"
        launch_wf = Workflow(
            id=uuid4(), name="launch", function_name="main", path=path,
            type="workflow", is_active=True, organization_id=org_b, solution_id=sol.id,
        )
        db.add(launch_wf)
        await db.flush()
        form = Form(
            id=uuid4(), name="xorg-launch-form", organization_id=org_b,
            solution_id=sol.id, workflow_id=f"{path}::main",
            launch_workflow_id=f"{path}::main", created_by="test",
        )
        db.add(form)
        await db.commit()

        r = e2e_client.post(
            f"/api/forms/{form.id}/startup",
            headers=platform_admin.headers,
            json={"input_data": {}},
        )
        assert not (
            r.status_code == 404 and "Launch workflow not found" in r.text
        ), (
            "cross-org caller could not resolve the install's own launch "
            f"workflow: {r.status_code} {r.text}"
        )
