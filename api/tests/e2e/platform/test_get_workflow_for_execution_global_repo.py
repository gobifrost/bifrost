"""get_workflow_for_execution returns the install's global_repo_access as
can_access_global_repo (one DB grab; the engine has no DB access)."""
from uuid import uuid4

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.execution.service import get_workflow_for_execution


async def _org(db):
    o = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
    db.add(o)
    await db.flush()
    return o


@pytest.mark.e2e
class TestGlobalRepoEnrichment:
    async def test_solution_workflow_carries_flag_true(self, db_session):
        db = db_session
        org = (await _org(db)).id
        sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S",
                       organization_id=org, global_repo_access=True)
        db.add(sol)
        await db.flush()
        wf = Workflow(id=uuid4(), name="w", function_name="main", path="workflows/w.py",
                      type="workflow", is_active=True, organization_id=org, solution_id=sol.id)
        db.add(wf)
        await db.flush()

        data = await get_workflow_for_execution(str(wf.id), db=db)
        assert data["solution_id"] == str(sol.id)
        assert data["can_access_global_repo"] is True

    async def test_solution_workflow_carries_flag_false(self, db_session):
        db = db_session
        org = (await _org(db)).id
        sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S",
                       organization_id=org, global_repo_access=False)
        db.add(sol)
        await db.flush()
        wf = Workflow(id=uuid4(), name="w", function_name="main", path="workflows/w.py",
                      type="workflow", is_active=True, organization_id=org, solution_id=sol.id)
        db.add(wf)
        await db.flush()

        data = await get_workflow_for_execution(str(wf.id), db=db)
        assert data["can_access_global_repo"] is False

    async def test_repo_workflow_flag_false(self, db_session):
        db = db_session
        wf = Workflow(id=uuid4(), name="w", function_name="main", path="workflows/w.py",
                      type="workflow", is_active=True, organization_id=None, solution_id=None)
        db.add(wf)
        await db.flush()

        data = await get_workflow_for_execution(str(wf.id), db=db)
        assert data["solution_id"] is None
        assert data["can_access_global_repo"] is False
