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


@pytest.mark.e2e
class TestConnectedBundleCompleteness:
    """read_workspace_bundle must collect apps + forms + agents, not just
    workflows/tables. Otherwise auto-pull reconcile DELETES a connected
    install's app/form/agent (Codex G4)."""

    async def test_bundle_includes_apps_forms_agents(self, tmp_path) -> None:
        from src.models.orm.solutions import Solution
        from src.services.solutions.git_sync import read_workspace_bundle

        (tmp_path / "bifrost.solution.yaml").write_text("slug: c\nname: C\nscope: global\n")
        (tmp_path / ".bifrost").mkdir()
        app_id = str(uuid.uuid4())
        (tmp_path / "apps" / "dash").mkdir(parents=True)
        (tmp_path / "apps" / "dash" / "index.html").write_text("<html></html>")
        (tmp_path / ".bifrost" / "apps.yaml").write_text(
            f"apps:\n  {app_id}:\n    id: {app_id}\n    slug: dash\n    name: Dash\n"
            f"    path: apps/dash\n    app_model: standalone_v2\n"
        )
        form_id = str(uuid.uuid4())
        (tmp_path / ".bifrost" / "forms.yaml").write_text(
            f"forms:\n  {form_id}:\n    id: {form_id}\n    name: intake\n    fields: []\n"
        )
        agent_id = str(uuid.uuid4())
        (tmp_path / ".bifrost" / "agents.yaml").write_text(
            f"agents:\n  {agent_id}:\n    id: {agent_id}\n    name: helper\n"
            f"    system_prompt: hi\n"
        )

        sol = Solution(id=uuid.uuid4(), slug="c", name="C", organization_id=None)
        bundle = read_workspace_bundle(sol, tmp_path)
        assert [a["id"] for a in bundle.apps] == [app_id]
        assert [f["id"] for f in bundle.forms] == [form_id]
        assert [a["id"] for a in bundle.agents] == [agent_id]


class TestReadWorkspaceBundleConfigSchemas:
    """read_workspace_bundle must collect config_schemas from .bifrost/configs.yaml.

    An empty list makes deploy's reconcile sweep DELETE every declaration the
    install owns, on every auto-pull sync (criterion 13 correctness invariant).
    """

    def test_read_workspace_bundle_collects_config_schemas(self, tmp_path) -> None:
        from src.models.orm.solutions import Solution
        from src.services.solutions.git_sync import read_workspace_bundle

        (tmp_path / "bifrost.solution.yaml").write_text("slug: cs\nname: CS\nscope: global\n")
        (tmp_path / ".bifrost").mkdir()
        (tmp_path / ".bifrost" / "configs.yaml").write_text(
            "configs:\n"
            "  API_KEY:\n"
            "    id: aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\n"
            "    key: API_KEY\n"
            "    type: secret\n"
            "    required: true\n"
            "    description: The API key\n"
        )

        sol = Solution(id=uuid.uuid4(), slug="cs", name="CS", organization_id=None)
        bundle = read_workspace_bundle(sol, tmp_path)
        assert len(bundle.config_schemas) == 1
        assert bundle.config_schemas[0]["key"] == "API_KEY"


@pytest.mark.e2e
class TestGitSyncRerun:
    async def test_rerun_when_trigger_arrives_mid_sync(self, db_session, monkeypatch):
        """Codex #13: a sync trigger arriving while a sync holds the lock must not
        be dropped. The holder re-checks a pending flag after finishing and
        re-syncs, so the newest commit is always deployed."""
        from src.core.redis_client import get_redis_client
        from src.services.solutions import git_sync as gs

        sol = Solution(
            id=uuid.uuid4(), slug=f"rr-{uuid.uuid4().hex[:8]}", name="RR",
            organization_id=None, git_connected=True, git_repo_url="file:///tmp/x",
        )
        db_session.add(sol)
        await db_session.flush()

        redis = await get_redis_client()._get_redis()
        pending_key = f"bifrost:solution:sync-pending:{sol.id}"
        await redis.delete(pending_key)

        calls = {"n": 0}

        async def _fake_run_once(db, solution):
            calls["n"] += 1
            # On the FIRST run, simulate a newer commit's trigger arriving while
            # the lock is held: it would set the pending flag.
            if calls["n"] == 1:
                await redis.set(pending_key, "1", ex=3600)

        monkeypatch.setattr(gs, "_run_sync_once", _fake_run_once)

        await gs.sync(db_session, sol)

        # Ran twice: once for the original, once for the queued newer commit.
        assert calls["n"] == 2
        # Flag cleared at the end (no infinite loop).
        assert await redis.get(pending_key) is None

    async def test_no_rerun_without_pending_trigger(self, db_session, monkeypatch):
        from src.core.redis_client import get_redis_client
        from src.services.solutions import git_sync as gs

        sol = Solution(
            id=uuid.uuid4(), slug=f"rr-{uuid.uuid4().hex[:8]}", name="RR",
            organization_id=None, git_connected=True, git_repo_url="file:///tmp/x",
        )
        db_session.add(sol)
        await db_session.flush()
        redis = await get_redis_client()._get_redis()
        await redis.delete(f"bifrost:solution:sync-pending:{sol.id}")

        calls = {"n": 0}

        async def _fake_run_once(db, solution):
            calls["n"] += 1

        monkeypatch.setattr(gs, "_run_sync_once", _fake_run_once)
        await gs.sync(db_session, sol)
        assert calls["n"] == 1  # no pending flag → single run
