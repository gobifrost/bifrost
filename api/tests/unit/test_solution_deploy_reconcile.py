"""Deploy = full-replace reconcile, scoped strictly to solution_id.

Criterion 10: redeploying a Solution with an entity removed deletes that entity
for THIS install only — never _repo/ and never another install. The scoping is
correct by construction: the deletion sweep is ``WHERE solution_id == sid AND id
NOT IN bundle_ids``, so _repo/ rows (solution_id IS NULL) and other installs
(different solution_id) are unreachable.
"""
from __future__ import annotations

import uuid as uuid_module
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

import pytest as _pytest

from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployConflict,
    SolutionDeployer,
    solution_entity_id,
)


def _wf_entry(wf_id: str, name: str) -> dict:
    """A minimal manifest-shaped workflow entry."""
    return {
        "id": wf_id,
        "name": name,
        "function_name": "run",
        "path": f"workflows/{name}.py",
        "type": "workflow",
    }


async def _active_wf_names(db, solution_id) -> set[str]:
    rows = (
        await db.execute(
            select(Workflow.name).where(
                Workflow.solution_id == solution_id,
                Workflow.is_active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    return set(rows)


@_pytest.fixture(autouse=True)
def _reset_redis_singleton():
    """The deployer's cache-sync opens the async Redis singleton, which binds
    its connection pool to the event loop that first used it. Across the full
    suite a prior test's (now-closed) loop leaves a stale singleton, so the
    next async Redis call raises "Event loop is closed".

    Drop the singleton *reference* synchronously (no await — awaiting .close()
    on a stale-loop client is itself what fails); the next get_redis_client()
    rebinds to the current loop and the stale connection is GC'd. These tests
    live in tests/unit/ where the global redis-isolation fixture is skipped."""
    import src.core.redis_client as rc

    rc._redis_client = None
    yield
    rc._redis_client = None


@pytest.mark.e2e
class TestSolutionDeployReconcile:
    async def _make_install(self, db, slug: str) -> Solution:
        sol = Solution(id=uuid4(), slug=slug, name=slug.upper(), organization_id=None)
        db.add(sol)
        await db.flush()
        return sol

    async def test_full_replace_scoped_to_solution(self, db_session) -> None:
        db = db_session
        sol = await self._make_install(db, f"recon-{uuid4().hex[:8]}")

        deployer = SolutionDeployer(db)

        # Deploy bundle A: two workflows.
        w1, w2 = str(uuid4()), str(uuid4())
        await deployer.deploy(
            SolutionBundle(
                solution=sol,
                python_files={
                    "workflows/w1.py": "def run():\n    return 1\n",
                    "workflows/w2.py": "def run():\n    return 2\n",
                },
                workflows=[_wf_entry(w1, "w1"), _wf_entry(w2, "w2")],
            )
        )
        await db.flush()
        assert await _active_wf_names(db, sol.id) == {"w1", "w2"}

        # Redeploy bundle B: w2 removed → deleted for THIS install only.
        await deployer.deploy(
            SolutionBundle(
                solution=sol,
                python_files={"workflows/w1.py": "def run():\n    return 1\n"},
                workflows=[_wf_entry(w1, "w1")],
            )
        )
        await db.flush()
        assert await _active_wf_names(db, sol.id) == {"w1"}

    async def test_repo_and_other_install_untouched(self, db_session) -> None:
        db = db_session

        # A _repo/ workflow named "w2" (solution_id IS NULL) — must survive.
        repo_wf = Workflow(
            id=uuid4(), name="w2", function_name="run", path="workflows/w2.py",
            type="workflow", organization_id=None, solution_id=None,
        )
        db.add(repo_wf)

        # A *second* install that also has a "w2".
        other = await self._make_install(db, f"other-{uuid4().hex[:8]}")
        other_w2 = uuid4()
        db.add(Workflow(
            id=other_w2, name="w2", function_name="run", path="workflows/w2.py",
            type="workflow", organization_id=None, solution_id=other.id,
        ))
        await db.flush()

        sol = await self._make_install(db, f"recon-{uuid4().hex[:8]}")
        deployer = SolutionDeployer(db)

        # Deploy w1+w2 to sol, then redeploy w1 only — sol's w2 is deleted.
        w1, w2 = str(uuid4()), str(uuid4())
        await deployer.deploy(SolutionBundle(
            solution=sol,
            python_files={"workflows/w1.py": "x", "workflows/w2.py": "y"},
            workflows=[_wf_entry(w1, "w1"), _wf_entry(w2, "w2")],
        ))
        await db.flush()
        await deployer.deploy(SolutionBundle(
            solution=sol,
            python_files={"workflows/w1.py": "x"},
            workflows=[_wf_entry(w1, "w1")],
        ))
        await db.flush()

        # sol now has only w1.
        assert await _active_wf_names(db, sol.id) == {"w1"}
        # _repo/ w2 untouched.
        assert (await db.get(Workflow, repo_wf.id)) is not None
        # other install's w2 untouched.
        assert (await db.get(Workflow, other_w2)) is not None

    async def test_deploy_stamps_scope_from_install(self, db_session) -> None:
        """Every deployed entity inherits the install's solution_id (and the
        install's org scope) — no per-entity scope binding (criterion 8)."""
        from src.models.orm.organizations import Organization

        db = db_session
        org = Organization(id=uuid4(), name=f"DeployOrg-{uuid4().hex[:8]}", created_by="test")
        db.add(org)
        await db.flush()

        org_install = Solution(
            id=uuid4(), slug=f"org-{uuid4().hex[:8]}", name="Org", organization_id=org.id
        )
        db.add(org_install)
        await db.flush()

        deployer = SolutionDeployer(db)
        w1 = str(uuid4())
        await deployer.deploy(SolutionBundle(
            solution=org_install,
            python_files={"workflows/w1.py": "x"},
            workflows=[_wf_entry(w1, "w1")],
        ))
        await db.flush()

        wf = await db.get(Workflow, solution_entity_id(org_install.id, UUID(w1)))
        assert wf is not None
        assert wf.solution_id == org_install.id
        assert wf.organization_id == org_install.organization_id

    async def test_unique_install_per_slug_and_scope(self, db_session) -> None:
        """Install identity is unique per (slug, scope) — §3.4 (Codex P2)."""
        from sqlalchemy.exc import IntegrityError

        db = db_session
        slug = f"dup-{uuid4().hex[:8]}"

        # Two global installs with the same slug must collide.
        db.add(Solution(id=uuid4(), slug=slug, name="A", organization_id=None))
        await db.flush()
        db.add(Solution(id=uuid4(), slug=slug, name="B", organization_id=None))
        with _pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

    async def test_unique_install_per_org_scope(self, db_session) -> None:
        """A Solution installs AT MOST ONCE per org (Codex #8 P1 root cause):
        two installs of one slug in the SAME org collide; the SAME slug in a
        DIFFERENT org is fine (independent installs)."""
        from sqlalchemy.exc import IntegrityError

        from src.models.orm.organizations import Organization

        db = db_session
        org_a = Organization(id=uuid4(), name=f"A-{uuid4().hex[:6]}", created_by="t")
        org_b = Organization(id=uuid4(), name=f"B-{uuid4().hex[:6]}", created_by="t")
        db.add_all([org_a, org_b])
        await db.flush()
        slug = f"dup-{uuid4().hex[:8]}"

        # Same slug in two DIFFERENT orgs — independent installs, allowed.
        db.add(Solution(id=uuid4(), slug=slug, name="A", organization_id=org_a.id))
        db.add(Solution(id=uuid4(), slug=slug, name="B", organization_id=org_b.id))
        await db.flush()  # must not raise

        # A SECOND install of the same slug in org-a — must collide.
        db.add(Solution(id=uuid4(), slug=slug, name="A2", organization_id=org_a.id))
        with _pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

    async def test_deploy_rejects_foreign_entity_id(self, db_session) -> None:
        """A bundle may not reuse a UUID owned by _repo/ or another install —
        that would re-stamp solution_id and hijack the row (Codex P1)."""
        db = db_session

        # A _repo/ workflow (solution_id IS NULL).
        repo_id = uuid4()
        db.add(Workflow(
            id=repo_id, name="victim", function_name="run", path="workflows/victim.py",
            type="workflow", organization_id=None, solution_id=None,
        ))
        await db.flush()

        sol = await self._make_install(db, f"thief-{uuid4().hex[:8]}")
        deployer = SolutionDeployer(db)

        # Bundle tries to claim the _repo/ workflow's id. The guard raises BEFORE
        # any UPDATE to the victim row, so no rollback is needed to prove it's safe.
        with _pytest.raises(SolutionDeployConflict):
            await deployer._upsert_workflows(sol, [_wf_entry(str(repo_id), "victim")])

        # The _repo/ row is untouched (still NULL solution_id).
        victim = await db.get(Workflow, repo_id)
        assert victim is not None
        assert victim.solution_id is None

    async def test_redeploy_changed_source_refreshes_cache(self, db_session) -> None:
        """Redeploying changed source must update the Redis module cache, not
        leave stale bytes for the 24h TTL (Codex P1)."""
        from src.core.module_cache import get_module
        from src.services.solutions.storage import SolutionStorage

        db = db_session
        sol = await self._make_install(db, f"cache-{uuid4().hex[:8]}")
        deployer = SolutionDeployer(db)
        w1 = str(uuid4())
        storage_key = SolutionStorage(sol.id)._key("workflows/w1.py")

        r1 = await deployer.deploy(SolutionBundle(
            solution=sol,
            python_files={"workflows/w1.py": "VALUE = 1\n"},
            workflows=[_wf_entry(w1, "w1")],
        ))
        await r1.finalize_s3()  # Python write is deferred to the S3 phase (P1-c).
        cached = await get_module(storage_key)
        assert cached is not None and "VALUE = 1" in cached["content"]

        # Redeploy with changed bytes — cache must reflect the new content.
        r2 = await deployer.deploy(SolutionBundle(
            solution=sol,
            python_files={"workflows/w1.py": "VALUE = 2\n"},
            workflows=[_wf_entry(w1, "w1")],
        ))
        await r2.finalize_s3()
        cached2 = await get_module(storage_key)
        assert cached2 is not None and "VALUE = 2" in cached2["content"]

    async def test_repo_resolver_ignores_solution_rows(self, db_session) -> None:
        """A _repo/-tier name/path lookup must not raise MultipleResultsFound
        when a solution reuses the same name/path (Codex P2). The cascade is
        _repo/-only; solution workflows resolve by id."""
        from src.repositories.workflows import WorkflowRepository

        db = db_session
        shared_path = f"workflows/dup_{uuid4().hex[:8]}.py"
        # _repo/ workflow.
        db.add(Workflow(
            id=uuid4(), name="dupname", function_name="run", path=shared_path,
            type="workflow", organization_id=None, solution_id=None,
        ))
        await db.flush()
        # Solution workflow with the SAME name + path.
        sol = await self._make_install(db, f"dupsol-{uuid4().hex[:8]}")
        db.add(Workflow(
            id=uuid4(), name="dupname", function_name="run", path=shared_path,
            type="workflow", organization_id=None, solution_id=sol.id,
        ))
        await db.flush()

        repo = WorkflowRepository(db, org_id=None, is_superuser=True)
        # Path-ref resolution returns exactly the _repo/ row (no MultipleResultsFound).
        resolved = await repo._resolve_by_path_ref(f"{shared_path}::run")
        assert resolved is not None
        assert resolved.solution_id is None
        # Name+type lookup likewise excludes the solution row.
        by_name = await repo.get_by_name_and_type("dupname", "workflow")
        assert by_name is not None
        assert by_name.solution_id is None


    async def test_workflow_metadata_full_replace(self, db_session) -> None:
        """Deploy carries deploy-owned workflow metadata (endpoint/timeout/
        category/tags) and full-replaces it on redeploy (Codex P2-i)."""
        from src.models.orm.workflows import Workflow

        db = db_session
        sol = await self._make_install(db, f"meta-{uuid4().hex[:8]}")
        wf_id = str(uuid4())
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            workflows=[{
                "id": wf_id, "name": "m", "function_name": "run", "path": "workflows/m.py",
                "endpoint_enabled": True, "timeout_seconds": 42,
                "category": "Billing", "tags": ["a", "b"],
            }],
        ))
        await db.flush()
        wf = await db.get(Workflow, solution_entity_id(sol.id, uuid_module.UUID(wf_id)))
        assert wf.endpoint_enabled is True
        assert wf.timeout_seconds == 42
        assert wf.category == "Billing"
        assert wf.tags == ["a", "b"]

        # Redeploy with the metadata removed → full-replaced to defaults.
        await SolutionDeployer(db).deploy(SolutionBundle(
            solution=sol,
            workflows=[{"id": wf_id, "name": "m", "function_name": "run", "path": "workflows/m.py"}],
        ))
        await db.flush()
        await db.refresh(wf)
        assert wf.endpoint_enabled is False
        assert wf.timeout_seconds == 1800
        assert wf.category == "General"
        assert wf.tags == []
