"""Per-install entity identity: deploy remaps bundle entity IDs to
``uuid5(install_id, original_manifest_id)``.

Why (the "fresh phone numbers per customer" property):
- Criterion 9 — the SAME workspace/repo/export can install into two scopes and
  each install gets its OWN, independent copy of every entity. A byte-identical
  bundle carries the same manifest UUIDs; without a remap the 2nd install would
  reuse the 1st install's row id and the ownership guard would (correctly) abort.
  The deterministic remap gives each install a distinct-but-stable id, so two
  installs no longer collide.
- Criterion 10 — a redeploy of the same install must keep wiring stable. uuid5 is
  a pure function of (install_id, manifest_id), so the same input yields the same
  id every time; an update doesn't scramble a customer's internal references.

The remap is INSTALL-TIME ONLY. The source repo / manifest keeps the original
author-time IDs; only the DB rows of a specific install carry the remapped id.
"""
from __future__ import annotations

from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import select

from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployer,
    solution_entity_id,
)


def _wf_entry(wf_id: str, name: str) -> dict:
    return {
        "id": wf_id,
        "name": name,
        "function_name": "run",
        "path": f"workflows/{name}.py",
        "type": "workflow",
    }


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    import src.core.redis_client as rc

    rc._redis_client = None
    yield
    rc._redis_client = None


async def _make_install(db, slug: str) -> Solution:
    sol = Solution(id=uuid4(), slug=slug, name=slug.upper(), organization_id=None)
    db.add(sol)
    await db.flush()
    return sol


def test_solution_entity_id_is_deterministic_uuid5():
    """The remap is a pure uuid5 of (install_id namespace, manifest id value)."""
    install = uuid4()
    manifest_id = uuid4()
    got = solution_entity_id(install, manifest_id)
    assert got == uuid5(install, str(manifest_id))
    # Stable across calls (criterion 10: redeploys don't scramble wiring).
    assert solution_entity_id(install, manifest_id) == got
    # Different install → different id (criterion 9: installs are independent).
    assert solution_entity_id(uuid4(), manifest_id) != got


@pytest.mark.e2e
class TestSolutionIdRemap:
    async def test_deployed_row_id_is_remapped(self, db_session) -> None:
        """The persisted workflow row uses uuid5(install_id, manifest_id), NOT
        the raw manifest id."""
        db = db_session
        sol = await _make_install(db, f"remap-{uuid4().hex[:8]}")
        manifest_id = str(uuid4())

        await SolutionDeployer(db).deploy(
            SolutionBundle(
                solution=sol,
                python_files={"workflows/w1.py": "def run():\n    return 1\n"},
                workflows=[_wf_entry(manifest_id, "w1")],
            )
        )
        await db.flush()

        expected = solution_entity_id(sol.id, UUID(manifest_id))
        rows = (
            await db.execute(
                select(Workflow.id).where(Workflow.solution_id == sol.id)
            )
        ).scalars().all()
        assert rows == [expected]
        # The raw manifest id was NOT used as the row id.
        assert UUID(manifest_id) not in rows

    async def test_same_bundle_two_installs_are_independent(self, db_session) -> None:
        """Criterion 9: a byte-identical bundle (same manifest UUIDs) deploys to
        two installs WITHOUT collision; each gets its own remapped row."""
        db = db_session
        sol_a = await _make_install(db, f"remap-a-{uuid4().hex[:8]}")
        sol_b = await _make_install(db, f"remap-b-{uuid4().hex[:8]}")
        manifest_id = str(uuid4())
        bundle_workflows = [_wf_entry(manifest_id, "w1")]
        pyfiles = {"workflows/w1.py": "def run():\n    return 1\n"}

        # Same bundle (same manifest id) → both installs succeed.
        await SolutionDeployer(db).deploy(
            SolutionBundle(solution=sol_a, python_files=pyfiles, workflows=bundle_workflows)
        )
        await db.flush()
        await SolutionDeployer(db).deploy(
            SolutionBundle(solution=sol_b, python_files=pyfiles, workflows=bundle_workflows)
        )
        await db.flush()

        id_a = solution_entity_id(sol_a.id, UUID(manifest_id))
        id_b = solution_entity_id(sol_b.id, UUID(manifest_id))
        assert id_a != id_b
        assert (
            await db.execute(select(Workflow.id).where(Workflow.solution_id == sol_a.id))
        ).scalars().all() == [id_a]
        assert (
            await db.execute(select(Workflow.id).where(Workflow.solution_id == sol_b.id))
        ).scalars().all() == [id_b]

    async def test_same_bundle_object_redeployed_is_stable(self, db_session) -> None:
        """Codex #8 P2: deploying the SAME SolutionBundle instance twice must not
        double-remap. The deployer must not mutate the caller's bundle, so the
        2nd deploy remaps the ORIGINAL manifest id again (same result), not the
        1st deploy's already-remapped id (which would scramble + delete-recreate)."""
        db = db_session
        sol = await _make_install(db, f"remap-{uuid4().hex[:8]}")
        manifest_id = str(uuid4())
        bundle = SolutionBundle(
            solution=sol,
            python_files={"workflows/w1.py": "def run():\n    return 1\n"},
            workflows=[_wf_entry(manifest_id, "w1")],
        )

        deployer = SolutionDeployer(db)
        await deployer.deploy(bundle)
        await db.flush()
        # The caller's bundle is UNCHANGED — its entity id is still the manifest id.
        assert bundle.workflows[0]["id"] == manifest_id

        # Deploy the SAME object again — stable id, no double-remap, no orphan.
        await deployer.deploy(bundle)
        await db.flush()
        assert bundle.workflows[0]["id"] == manifest_id
        rows = (
            await db.execute(select(Workflow.id).where(Workflow.solution_id == sol.id))
        ).scalars().all()
        assert rows == [solution_entity_id(sol.id, UUID(manifest_id))]

    async def test_redeploy_keeps_stable_id(self, db_session) -> None:
        """Criterion 10: redeploying the same install reuses the same row id
        (deterministic), so internal wiring survives an update."""
        db = db_session
        sol = await _make_install(db, f"remap-{uuid4().hex[:8]}")
        manifest_id = str(uuid4())
        pyfiles = {"workflows/w1.py": "def run():\n    return 1\n"}
        wfs = [_wf_entry(manifest_id, "w1")]

        await SolutionDeployer(db).deploy(
            SolutionBundle(solution=sol, python_files=pyfiles, workflows=wfs)
        )
        await db.flush()
        first = (
            await db.execute(select(Workflow.id).where(Workflow.solution_id == sol.id))
        ).scalars().one()

        # Redeploy the same bundle.
        await SolutionDeployer(db).deploy(
            SolutionBundle(solution=sol, python_files=pyfiles, workflows=wfs)
        )
        await db.flush()
        second = (
            await db.execute(select(Workflow.id).where(Workflow.solution_id == sol.id))
        ).scalars().one()

        assert first == second == solution_entity_id(sol.id, UUID(manifest_id))
