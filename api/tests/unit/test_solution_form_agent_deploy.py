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
    solution_entity_id,
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
                    "fields": [
                        {"name": "email", "label": "Email", "type": "text", "required": True},
                    ],
                },
            }],
        ))
        await db.flush()
        expected_id = solution_entity_id(sol.id, uuid.UUID(fid))
        form = await db.get(Form, expected_id)
        assert form is not None
        assert form.solution_id == sol.id
        assert form.organization_id == sol.organization_id
        assert result.forms_upserted == 1
        # The portable fields (from form_schema.fields) were created.
        from sqlalchemy import select as _select

        from src.models.orm.forms import FormField
        names = (await db.execute(
            _select(FormField.name).where(FormField.form_id == expected_id)
        )).scalars().all()
        assert "email" in names, "form fields not deployed from form_schema.fields"

    async def test_deploy_stamps_form_agent_access_level_from_manifest(self, db_session):
        """Codex #14: access_level is deploy-owned for solution forms/agents — the
        manifest's value must be applied on deploy AND changeable on redeploy (the
        entity is read-only outside deploy, so deploy is the only writer)."""
        db = db_session
        sol = await self._install(db)
        fid, aid = str(uuid.uuid4()), str(uuid.uuid4())

        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            forms=[{"id": fid, "name": "intake", "access_level": "authenticated",
                    "form_schema": {"fields": []}}],
            agents=[{"id": aid, "name": "a", "system_prompt": "hi",
                     "access_level": "authenticated"}],
        ))
        await db.flush()
        form = await db.get(Form, solution_entity_id(sol.id, uuid.UUID(fid)))
        agent = await db.get(Agent, solution_entity_id(sol.id, uuid.UUID(aid)))
        assert form.access_level.value == "authenticated"
        assert agent.access_level.value == "authenticated"

        # Redeploy with a CHANGED access_level → applied (not stuck at first value).
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            forms=[{"id": fid, "name": "intake", "access_level": "role_based",
                    "form_schema": {"fields": []}}],
            agents=[{"id": aid, "name": "a", "system_prompt": "hi",
                     "access_level": "role_based"}],
        ))
        await db.flush()
        await db.refresh(form)
        await db.refresh(agent)
        assert form.access_level.value == "role_based"
        assert agent.access_level.value == "role_based"

    async def test_deploy_agent_stamps_solution_and_scope(self, db_session):
        db = db_session
        sol = await self._install(db)
        aid = str(uuid.uuid4())
        result = await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            agents=[{"id": aid, "name": "helper", "system_prompt": "You help."}],
        ))
        await db.flush()
        agent = await db.get(Agent, solution_entity_id(sol.id, uuid.UUID(aid)))
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
        expected_id = solution_entity_id(sol.id, uuid.UUID(fid))
        assert await db.get(Form, expected_id) is not None
        result = await SolutionDeployer(db).deploy(SolutionBundle(solution=sol, forms=[]))
        await db.flush()
        assert await db.get(Form, expected_id) is None
        assert result.forms_deleted == 1

    async def test_repo_form_id_collision_raises_conflict(self, db_session):
        db = db_session
        sol = await self._install(db)
        # A _repo/ form already owns the REMAPPED id this bundle's manifest id
        # deploys into. The manifest id is remapped before the ownership guard,
        # so the pre-seeded _repo/ row must carry the per-install remapped id to
        # collide — a bundle may not hijack a _repo/-owned remapped id.
        manifest_id = uuid.uuid4()
        repo_id = solution_entity_id(sol.id, manifest_id)
        db.add(Form(id=repo_id, name="repo-form", organization_id=None, solution_id=None, created_by="dev@x"))
        await db.flush()
        with pytest.raises(SolutionDeployConflict):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol, forms=[{"id": str(manifest_id), "name": "repo-form", "fields": []}],
            ))


@pytest.mark.e2e
class TestAgentBindingsDeploy:
    """Deployed agents must carry their tool/knowledge bindings (Codex P2-h),
    not just the bare row — proves the indexer-delegation handles content."""

    async def test_agent_tools_and_knowledge_deploy(self, db_session):
        from src.models.orm.agents import AgentTool
        from src.models.orm.workflows import Workflow
        from sqlalchemy import select as _select

        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"ab-{uuid.uuid4().hex[:8]}", name="AB", organization_id=None)
        db.add(sol)
        # A workflow the agent will bind as a tool.
        wf_id = uuid.uuid4()
        db.add(Workflow(
            id=wf_id, name="tool_wf", function_name="run", path="workflows/t.py",
            type="workflow", organization_id=None, solution_id=None, is_active=True,
        ))
        await db.flush()

        aid = str(uuid.uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            agents=[{
                "id": aid, "name": "bound", "system_prompt": "hi",
                "tool_ids": [str(wf_id)],
                "knowledge_sources": ["kb-1"],
                "system_tools": ["web_search"],
            }],
        ))
        await db.flush()

        agent_id = solution_entity_id(sol.id, uuid.UUID(aid))
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        assert agent.solution_id == sol.id
        # Portable manifest content (knowledge/system_tools) deployed.
        assert agent.knowledge_sources == ["kb-1"]
        assert agent.system_tools == ["web_search"]
        # Tool junction row created (the key P2-h binding).
        tool_wf_ids = (await db.execute(
            _select(AgentTool.workflow_id).where(AgentTool.agent_id == agent_id)
        )).scalars().all()
        assert wf_id in tool_wf_ids, "agent tool binding dropped on deploy"


@pytest.mark.e2e
class TestSolutionRoleBindingsDeploy:
    """Deploy must sync manifest roles into the FormRole/AgentRole/AppRole/
    WorkflowRole junctions (Codex P1-d). The role-mutation endpoints are
    read-only for solution-managed entities, so deploy is the ONLY writer of
    these bindings — a role_based entity deployed without them is inaccessible
    and uncorrectable except by another deploy. ``role_names`` resolve against
    the install's org; a redeploy with changed roles full-replaces the rows.
    """

    async def _install(self, db) -> Solution:
        sol = Solution(
            id=uuid.uuid4(), slug=f"rb-{uuid.uuid4().hex[:8]}", name="RB",
            organization_id=None,
        )
        db.add(sol)
        await db.flush()
        return sol

    async def _role(self, db, name: str):
        from src.models.orm.users import Role

        r = Role(id=uuid.uuid4(), name=name, created_by="dev@x")
        db.add(r)
        await db.flush()
        return r

    async def test_form_role_names_resolve_and_sync(self, db_session):
        from sqlalchemy import select as _select

        from src.models.orm.forms import FormRole

        db = db_session
        sol = await self._install(db)
        role = await self._role(db, f"Support-{uuid.uuid4().hex[:6]}")
        fid = str(uuid.uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            forms=[{
                "id": fid, "name": "intake", "access_level": "role_based",
                "role_names": [role.name],
                "form_schema": {"fields": []},
            }],
        ))
        await db.flush()
        role_ids = (await db.execute(
            _select(FormRole.role_id).where(
                FormRole.form_id == solution_entity_id(sol.id, uuid.UUID(fid))
            )
        )).scalars().all()
        assert role.id in role_ids, "form role binding not synced on deploy"

    async def test_agent_app_workflow_roles_sync(self, db_session):
        from sqlalchemy import select as _select

        from src.models.orm.agents import AgentRole
        from src.models.orm.app_roles import AppRole
        from src.models.orm.workflow_roles import WorkflowRole

        db = db_session
        sol = await self._install(db)
        role = await self._role(db, f"Ops-{uuid.uuid4().hex[:6]}")

        aid, app_id, wf_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            agents=[{
                "id": aid, "name": "a", "system_prompt": "hi",
                "access_level": "role_based", "role_names": [role.name],
            }],
            apps=[{
                "id": app_id, "slug": "dash", "name": "Dash",
                # Solution apps are standalone_v2; prebuilt dist skips the vite build.
                "app_model": "standalone_v2", "access_level": "role_based",
                "dist_files": {"index.html": "<html></html>"},
                "role_names": [role.name],
            }],
            workflows=[{
                "id": wf_id, "name": "w", "function_name": "run",
                "path": "workflows/w.py", "type": "workflow",
                "access_level": "role_based", "role_names": [role.name],
            }],
        ))
        await db.flush()

        agent_roles = (await db.execute(
            _select(AgentRole.role_id).where(
                AgentRole.agent_id == solution_entity_id(sol.id, uuid.UUID(aid))
            )
        )).scalars().all()
        app_roles = (await db.execute(
            _select(AppRole.role_id).where(
                AppRole.app_id == solution_entity_id(sol.id, uuid.UUID(app_id))
            )
        )).scalars().all()
        wf_roles = (await db.execute(
            _select(WorkflowRole.role_id).where(
                WorkflowRole.workflow_id == solution_entity_id(sol.id, uuid.UUID(wf_id))
            )
        )).scalars().all()
        assert role.id in agent_roles, "agent role binding not synced"
        assert role.id in app_roles, "app role binding not synced"
        assert role.id in wf_roles, "workflow role binding not synced"

    async def test_redeploy_replaces_role_bindings(self, db_session):
        from sqlalchemy import select as _select

        from src.models.orm.forms import FormRole

        db = db_session
        sol = await self._install(db)
        r1 = await self._role(db, f"R1-{uuid.uuid4().hex[:6]}")
        r2 = await self._role(db, f"R2-{uuid.uuid4().hex[:6]}")
        fid = str(uuid.uuid4())

        async def _deploy_with(role_name: str):
            await SolutionDeployer(db).deploy(SolutionBundle(
                solution=sol,
                forms=[{
                    "id": fid, "name": "intake", "access_level": "role_based",
                    "role_names": [role_name], "form_schema": {"fields": []},
                }],
            ))
            await db.flush()

        await _deploy_with(r1.name)
        await _deploy_with(r2.name)  # full-replace: r1 binding must be gone
        role_ids = set((await db.execute(
            _select(FormRole.role_id).where(
                FormRole.form_id == solution_entity_id(sol.id, uuid.UUID(fid))
            )
        )).scalars().all())
        assert role_ids == {r2.id}, "redeploy did not full-replace role bindings"
