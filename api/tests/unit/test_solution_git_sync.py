"""Sub-plan 5 — Git-connected mode (criterion 13).

A git-connected install has exactly one writer: auto-pull from its repo.
- ``deploy_from_workspace`` reads a checked-out Solution workspace (Python source
  + ``.bifrost/*.yaml`` manifest) and deploys it via SolutionDeployer.
- ``bifrost deploy`` / the REST deploy endpoint are REFUSED for a connected
  install (the one-writer invariant; verified in the e2e).
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.git_sync import (
    NotASolutionWorkspace,
    deploy_from_workspace,
)


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    import src.core.redis_client as rc
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    rc._redis_client = None
    yield
    rc._redis_client = None


@pytest.mark.e2e
class TestDeployFromWorkspace:
    async def test_reads_workspace_and_deploys(self, db_session, tmp_path) -> None:
        from sqlalchemy import select

        db = db_session
        sol = Solution(
            id=uuid.uuid4(), slug=f"git-{uuid.uuid4().hex[:8]}", name="G",
            organization_id=None, git_connected=True,
            git_repo_url="https://example.com/x.git",
        )
        db.add(sol)
        await db.flush()

        # Lay out a checked-out Solution workspace (must have the descriptor).
        (tmp_path / "bifrost.solution.yaml").write_text(
            f"slug: {sol.slug}\nname: G\nscope: global\n"
        )
        wf_id = str(uuid.uuid4())
        (tmp_path / "workflows").mkdir()
        (tmp_path / "workflows" / "w.py").write_text(
            "from bifrost import workflow\n@workflow\nasync def w():\n    return {}\n"
        )
        (tmp_path / ".bifrost").mkdir()
        (tmp_path / ".bifrost" / "workflows.yaml").write_text(
            f"workflows:\n  {wf_id}:\n    id: {wf_id}\n    name: gitwf\n"
            f"    function_name: w\n    path: workflows/w.py\n    type: workflow\n"
        )

        await deploy_from_workspace(db, sol, tmp_path)
        await db.flush()

        names = (
            await db.execute(select(Workflow.name).where(Workflow.solution_id == sol.id))
        ).scalars().all()
        assert names == ["gitwf"]

    async def test_refuses_non_solution_checkout(self, db_session, tmp_path) -> None:
        """A checkout with no bifrost.solution.yaml must NOT full-replace the
        install down to empty (Codex Sub-plan 5 P1)."""
        from sqlalchemy import select

        db = db_session
        sol = Solution(
            id=uuid.uuid4(), slug=f"git-{uuid.uuid4().hex[:8]}", name="G",
            organization_id=None, git_connected=True, git_repo_url="https://example.com/x.git",
        )
        db.add(sol)
        # Pre-existing deployed workflow that must survive a bad sync.
        keep_id = uuid.uuid4()
        db.add(Workflow(
            id=keep_id, name="keepme", function_name="run", path="workflows/keepme.py",
            type="workflow", organization_id=None, solution_id=sol.id,
        ))
        await db.flush()

        # tmp_path has NO bifrost.solution.yaml.
        with pytest.raises(NotASolutionWorkspace):
            await deploy_from_workspace(db, sol, tmp_path)

        # The existing install is untouched.
        survivors = (
            await db.execute(select(Workflow.name).where(Workflow.solution_id == sol.id))
        ).scalars().all()
        assert survivors == ["keepme"]
