"""Path-ref resolution must reach a SOLUTION-managed workflow within the
caller's scope (R7-P1-c).

A v2 Solution app (and forms, and any path-ref caller) references a workflow by
``path::function_name`` — it cannot hard-code the per-install UUID, which it
won't know until install (see the uuid5 remap). So ``WorkflowRepository.resolve``
must resolve that path ref to the install's OWN solution-managed workflow, not
exclude solution rows and 404.

Disambiguation when a ``_repo/`` row and a solution row share a path: prefer the
solution-managed row in the caller's org (the app's own install). A lone
``_repo/`` row still resolves (unchanged behavior).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.repositories.workflows import WorkflowRepository


async def _add_org(db) -> Organization:
    org = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
    db.add(org)
    await db.flush()
    return org


async def _add_solution(db, org_id):
    sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=org_id)
    db.add(sol)
    await db.flush()
    return sol


async def _add_workflow(db, *, org_id, solution_id, path, fn="main", name=None):
    wf = Workflow(
        id=uuid4(),
        name=name or path,
        function_name=fn,
        path=path,
        type="workflow",
        is_active=True,
        organization_id=org_id,
        solution_id=solution_id,
    )
    db.add(wf)
    await db.flush()
    return wf


@pytest.mark.e2e
class TestPathRefSolutionScope:
    async def test_resolves_solution_managed_workflow_by_path(self, db_session) -> None:
        """The deployed Solution workflow is reachable from its own app's
        path-ref — previously excluded, so it 404'd."""
        db = db_session
        org = (await _add_org(db)).id
        sol = await _add_solution(db, org)
        wf = await _add_workflow(
            db, org_id=org, solution_id=sol.id, path="workflows/foo.py"
        )

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        got = await repo.resolve("workflows/foo.py::main")
        assert got is not None
        assert got.id == wf.id

    async def test_prefers_solution_row_when_repo_shares_path(self, db_session) -> None:
        """A _repo/ row and a solution row share a path: resolve to the
        solution row (the caller's install), NOT MultipleResultsFound."""
        db = db_session
        org = (await _add_org(db)).id
        sol = await _add_solution(db, org)
        # _repo/ row (global, solution_id NULL) sharing the path.
        await _add_workflow(
            db, org_id=None, solution_id=None, path="workflows/foo.py", name="repo-foo"
        )
        sol_wf = await _add_workflow(
            db, org_id=org, solution_id=sol.id, path="workflows/foo.py", name="sol-foo"
        )

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        # Must NOT raise MultipleResultsFound, and must pick the solution row.
        got = await repo.resolve("workflows/foo.py::main")
        assert got is not None
        assert got.id == sol_wf.id

    async def test_repo_only_path_still_resolves(self, db_session) -> None:
        """A lone _repo/ workflow (no solution row) resolves unchanged."""
        db = db_session
        repo_wf = await _add_workflow(
            db, org_id=None, solution_id=None, path="workflows/bar.py", name="repo-bar"
        )

        repo = WorkflowRepository(db, org_id=uuid4(), is_superuser=True)
        got = await repo.resolve("workflows/bar.py::main")
        assert got is not None
        assert got.id == repo_wf.id

    async def test_global_caller_prefers_repo_over_global_solution(self, db_session) -> None:
        """A GLOBAL/system caller (org_id=None) resolving a path shared by a
        _repo/ row and a GLOBAL solution row gets the _repo/ row — the shared
        library must not be hijacked by a global Solution reusing the path."""
        db = db_session
        # _repo/ row (global, solution_id NULL).
        repo_wf = await _add_workflow(
            db, org_id=None, solution_id=None, path="workflows/foo.py", name="repo-foo"
        )
        # A GLOBAL-scoped solution (organization_id None) sharing the path.
        sol = await _add_solution(db, None)
        await _add_workflow(
            db, org_id=None, solution_id=sol.id, path="workflows/foo.py", name="sol-foo"
        )

        repo = WorkflowRepository(db, org_id=None, is_superuser=True)
        got = await repo.resolve("workflows/foo.py::main")
        assert got is not None
        assert got.id == repo_wf.id
        assert got.solution_id is None

    async def test_other_orgs_solution_row_not_resolved(self, db_session) -> None:
        """A solution workflow in a DIFFERENT org is not reachable — scope still
        applies (each install resolves its own copy)."""
        db = db_session
        other_org = (await _add_org(db)).id
        sol = await _add_solution(db, other_org)
        await _add_workflow(
            db, org_id=other_org, solution_id=sol.id, path="workflows/foo.py"
        )

        # Caller is a regular user in a DIFFERENT org (not superuser, so no bypass).
        repo = WorkflowRepository(db, org_id=uuid4(), is_superuser=False)
        got = await repo.resolve("workflows/foo.py::main")
        assert got is None
