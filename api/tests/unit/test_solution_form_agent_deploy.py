"""Sub-plan G2 — deploy forms + agents as solution-managed (criteria 6, 10).

The spec's managed entity set is workflows, apps, forms, agents, tables. Forms
and agents must deploy: stamped with solution_id + inherited scope, read-only,
and scoped-reconciled (removed from a redeploy → deleted for this install only).
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.agents import Agent
from src.models.orm.forms import Form
from src.models.orm.solutions import Solution
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployConflict,
    SolutionDeployer,
)


@pytest.fixture(autouse=True)
def _guard():
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


@pytest.mark.e2e
class TestSolutionFormAgentDeploy:
    async def _install(self, db) -> Solution:
        sol = Solution(
            id=uuid.uuid4(), slug=f"fa-{uuid.uuid4().hex[:8]}", name="FA",
            organization_id=None,
        )
        db.add(sol)
        await db.flush()
        return sol

    async def test_deploy_form_stamps_solution_and_scope(self, db_session):
        db = db_session
        sol = await self._install(db)
        fid = str(uuid.uuid4())
        # Use the REAL manifest shape: fields live under form_schema.fields
        # (NOT a top-level `fields` key — reading the wrong key silently drops
        # all fields, which an earlier version of this test masked).
        result = await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            forms=[{
                "id": fid, "name": "intake",
                "form_schema": {
                    "fields": [{"name": "email", "type": "text", "required": True}],
                },
            }],
        ))
        await db.flush()
        form = await db.get(Form, uuid.UUID(fid))
        assert form is not None
        assert form.solution_id == sol.id
        assert form.organization_id == sol.organization_id
        assert result.forms_upserted == 1
        # The portable fields (from form_schema.fields) were created.
        from sqlalchemy import select as _select

        from src.models.orm.forms import FormField
        names = (await db.execute(
            _select(FormField.name).where(FormField.form_id == uuid.UUID(fid))
        )).scalars().all()
        assert "email" in names, "form fields not deployed from form_schema.fields"

    async def test_deploy_agent_stamps_solution_and_scope(self, db_session):
        db = db_session
        sol = await self._install(db)
        aid = str(uuid.uuid4())
        result = await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            agents=[{"id": aid, "name": "helper", "system_prompt": "You help."}],
        ))
        await db.flush()
        agent = await db.get(Agent, uuid.UUID(aid))
        assert agent is not None
        assert agent.solution_id == sol.id
        assert agent.organization_id == sol.organization_id
        assert result.agents_upserted == 1

    async def test_redeploy_without_form_removes_for_this_install(self, db_session):
        db = db_session
        sol = await self._install(db)
        fid = str(uuid.uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol, forms=[{"id": fid, "name": "gone", "fields": []}],
        ))
        await db.flush()
        assert await db.get(Form, uuid.UUID(fid)) is not None
        result = await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, forms=[]))
        await db.flush()
        assert await db.get(Form, uuid.UUID(fid)) is None
        assert result.forms_deleted == 1

    async def test_repo_form_id_collision_raises_conflict(self, db_session):
        db = db_session
        sol = await self._install(db)
        fid = uuid.uuid4()
        db.add(Form(id=fid, name="repo-form", organization_id=None, solution_id=None, created_by="dev@x"))
        await db.flush()
        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol, forms=[{"id": str(fid), "name": "repo-form", "fields": []}],
            ))
