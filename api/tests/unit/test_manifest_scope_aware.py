"""Scope-aware manifest generation (success-criteria §5 prereq).

generate_manifest historically dumps ALL orgs' entities. A per-scope Solution
export must NOT cross-contaminate tenants: generate_manifest(db, solution_id=X)
returns only that install's solution-managed entities (workflows/forms/agents/
apps/tables), never _repo/ rows or other installs.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.manifest_generator import generate_manifest


@pytest.mark.e2e
class TestScopeAwareManifest:

    async def test_workspace_export_serializes_a_queried_agent_model_profile(
        self, db_session
    ) -> None:
        """Manifest generation explicitly loads the profile used for its name."""
        from src.models.orm.agents import Agent
        from src.models.orm.ai_models import AIModelProfile, AIProviderConnection

        connection = AIProviderConnection(
            id=uuid.uuid4(),
            name=f"manifest-provider-{uuid.uuid4().hex[:8]}",
            provider="openai",
            encrypted_api_key="encrypted-fixture-key",
        )
        profile = AIModelProfile(
            id=uuid.uuid4(),
            name=f"manifest-profile-{uuid.uuid4().hex[:8]}",
            connection_id=connection.id,
            model="gpt-4o-mini",
        )
        agent = Agent(
            id=uuid.uuid4(),
            name=f"manifest-agent-{uuid.uuid4().hex[:8]}",
            system_prompt="Serialize my configured model profile.",
            created_by="test",
            llm_profile_id=profile.id,
        )
        db_session.add_all([connection, profile, agent])
        await db_session.flush()
        agent_id = agent.id
        profile_name = profile.name
        db_session.expunge_all()

        manifest = await generate_manifest(db_session)

        assert manifest.agents[str(agent_id)].llm_profile == profile_name

    async def test_solution_filter_excludes_repo_and_other_installs(self, db_session) -> None:
        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"man-{uuid.uuid4().hex[:8]}", name="M", organization_id=None)
        other = Solution(id=uuid.uuid4(), slug=f"oth-{uuid.uuid4().hex[:8]}", name="O", organization_id=None)
        db.add_all([sol, other])
        await db.flush()

        mine = uuid.uuid4()
        db.add_all([
            Workflow(id=mine, name=f"mine_{mine.hex[:6]}", function_name="run",
                     path=f"workflows/mine_{mine.hex[:6]}.py", type="workflow",
                     organization_id=None, solution_id=sol.id),
            Workflow(id=uuid.uuid4(), name=f"repo_{uuid.uuid4().hex[:6]}", function_name="run",
                     path=f"workflows/repo_{uuid.uuid4().hex[:6]}.py", type="workflow",
                     organization_id=None, solution_id=None),
            Workflow(id=uuid.uuid4(), name=f"other_{uuid.uuid4().hex[:6]}", function_name="run",
                     path=f"workflows/other_{uuid.uuid4().hex[:6]}.py", type="workflow",
                     organization_id=None, solution_id=other.id),
        ])
        await db.flush()

        manifest = await generate_manifest(db, solution_id=sol.id)
        wf_ids = {w.id for w in manifest.workflows.values()}
        assert wf_ids == {str(mine)}

    async def test_no_solution_id_is_repo_scoped(self, db_session) -> None:
        """generate_manifest(db) with no solution_id is a WORKSPACE (_repo/)
        regen: it includes _repo/ workflows but EXCLUDES solution-managed rows,
        so a normal .bifrost/ regen never serializes deploy-owned entities into
        the workspace git/import flow (Codex #16)."""
        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"man-{uuid.uuid4().hex[:8]}", name="M", organization_id=None)
        db.add(sol)
        await db.flush()

        repo_id, sol_wf_id = uuid.uuid4(), uuid.uuid4()
        db.add_all([
            Workflow(
                id=repo_id, name=f"legacy_{repo_id.hex[:6]}", function_name="run",
                path=f"workflows/legacy_{repo_id.hex[:6]}.py", type="workflow",
                organization_id=None, solution_id=None,
            ),
            Workflow(
                id=sol_wf_id, name=f"sol_{sol_wf_id.hex[:6]}", function_name="run",
                path=f"workflows/sol_{sol_wf_id.hex[:6]}.py", type="workflow",
                organization_id=None, solution_id=sol.id,
            ),
        ])
        await db.flush()
        manifest = await generate_manifest(db)
        assert str(repo_id) in manifest.workflows          # _repo/ included
        assert str(sol_wf_id) not in manifest.workflows    # solution-managed excluded
