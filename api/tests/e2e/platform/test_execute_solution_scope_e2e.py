"""A solution form's /execute resolves the install's own workflow, not _repo/."""
from uuid import uuid4

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.forms import Form
from src.models.orm.workflows import Workflow
from src.repositories.workflows import WorkflowRepository
from src.routers.workflows import _derive_solution_scope


async def _org(db):
    o = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
    db.add(o)
    await db.flush()
    return o


@pytest.mark.e2e
class TestExecuteSolutionScopeE2E:
    async def test_form_resolves_own_install_workflow(self, db_session):
        db = db_session
        org = (await _org(db)).id
        sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=org)
        db.add(sol)
        await db.flush()

        # A _repo/ workflow and the install's own workflow share the path.
        repo_wf = Workflow(id=uuid4(), name="repo", function_name="main",
                           path="workflows/foo.py", type="workflow", is_active=True,
                           organization_id=None, solution_id=None)
        own_wf = Workflow(id=uuid4(), name="own", function_name="main",
                          path="workflows/foo.py", type="workflow", is_active=True,
                          organization_id=org, solution_id=sol.id)
        db.add_all([repo_wf, own_wf])
        await db.flush()

        form = Form(id=uuid4(), name="f", organization_id=org, solution_id=sol.id,
                    workflow_id="workflows/foo.py::main", created_by="test")
        db.add(form)
        await db.flush()

        # Router sequence: derive scope from form_id, then resolve.
        scope = await _derive_solution_scope(db, solution_id=None, form_id=str(form.id), app_id=None)
        assert scope == sol.id
        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        got = await repo.resolve("workflows/foo.py::main", solution_scope=scope)
        assert got is not None
        assert got.id == own_wf.id, "form must resolve its install's workflow, not _repo/"

    async def test_non_solution_form_resolves_repo(self, db_session):
        db = db_session
        org = (await _org(db)).id
        repo_wf = Workflow(id=uuid4(), name="repo", function_name="main",
                           path="workflows/bar.py", type="workflow", is_active=True,
                           organization_id=None, solution_id=None)
        db.add(repo_wf)
        await db.flush()
        form = Form(id=uuid4(), name="f", organization_id=org, solution_id=None,
                    workflow_id="workflows/bar.py::main", created_by="test")
        db.add(form)
        await db.flush()

        scope = await _derive_solution_scope(db, solution_id=None, form_id=str(form.id), app_id=None)
        assert scope is None
        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        got = await repo.resolve("workflows/bar.py::main", solution_scope=scope)
        assert got is not None and got.id == repo_wf.id
