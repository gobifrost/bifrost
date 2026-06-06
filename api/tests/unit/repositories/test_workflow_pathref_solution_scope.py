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

    async def test_no_scope_caller_prefers_repo_on_shared_path(self, db_session) -> None:
        """A _repo/ row and a solution row share a path, and the caller passes NO
        install scope: prefer the _repo/ row (deterministic). Reaching the
        solution row requires an explicit solution_scope — see the install-scope
        tests below. (Earlier this preferred the solution row by org alone, which
        Codex #8 P1 proved non-deterministic with two same-org installs.)"""
        db = db_session
        org = (await _add_org(db)).id
        sol = await _add_solution(db, org)
        repo_wf = await _add_workflow(
            db, org_id=None, solution_id=None, path="workflows/foo.py", name="repo-foo"
        )
        await _add_workflow(
            db, org_id=org, solution_id=sol.id, path="workflows/foo.py", name="sol-foo"
        )

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        # No solution_scope → deterministic _repo/ row (never MultipleResultsFound).
        got = await repo.resolve("workflows/foo.py::main")
        assert got is not None
        assert got.id == repo_wf.id

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

    async def test_install_scope_disambiguates_two_solutions_same_org(self, db_session) -> None:
        """Codex #8 P1: two DIFFERENT solution installs in the SAME org both ship
        workflows/main.py::main. With the caller's install scope, the resolver
        returns THAT install's workflow — deterministically, not 'whichever row
        the DB returned first'."""
        db = db_session
        org = (await _add_org(db)).id
        sol_a = await _add_solution(db, org)
        sol_b = await _add_solution(db, org)
        wf_a = await _add_workflow(db, org_id=org, solution_id=sol_a.id, path="workflows/main.py")
        wf_b = await _add_workflow(db, org_id=org, solution_id=sol_b.id, path="workflows/main.py")

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        got_a = await repo.resolve("workflows/main.py::main", solution_scope=sol_a.id)
        got_b = await repo.resolve("workflows/main.py::main", solution_scope=sol_b.id)
        assert got_a is not None and got_a.id == wf_a.id
        assert got_b is not None and got_b.id == wf_b.id

    async def test_install_scope_falls_back_to_repo_when_own_absent(self, db_session) -> None:
        """An install whose bundle does NOT ship a given path resolves the global
        _repo/ workflow at that path (the app referenced a shared-library path)."""
        db = db_session
        org = (await _add_org(db)).id
        sol = await _add_solution(db, org)
        # No solution workflow at this path; only a _repo/ one.
        repo_wf = await _add_workflow(
            db, org_id=None, solution_id=None, path="workflows/shared.py", name="repo"
        )
        # An unrelated solution workflow at a DIFFERENT path (same install).
        await _add_workflow(db, org_id=org, solution_id=sol.id, path="workflows/own.py")

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        got = await repo.resolve("workflows/shared.py::main", solution_scope=sol.id)
        assert got is not None and got.id == repo_wf.id

    async def test_install_scope_prefers_own_over_repo_on_shared_path(self, db_session) -> None:
        """When a _repo/ row and the caller's OWN solution row share a path, the
        install-scoped resolve returns the install's own workflow."""
        db = db_session
        org = (await _add_org(db)).id
        sol = await _add_solution(db, org)
        await _add_workflow(db, org_id=None, solution_id=None, path="workflows/foo.py", name="repo")
        own = await _add_workflow(db, org_id=org, solution_id=sol.id, path="workflows/foo.py", name="own")

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        got = await repo.resolve("workflows/foo.py::main", solution_scope=sol.id)
        assert got is not None and got.id == own.id

    async def test_scoped_caller_never_resolves_a_sibling_install(self, db_session) -> None:
        """Codex #11: a scoped caller whose path is absent from ITS OWN install
        must NOT fall back to a SIBLING install's workflow at the same path — it
        resolves to None (404), preserving the self-contained boundary. (A typo
        or stale ref in app A must never execute app B's workflow.)"""
        db = db_session
        org = (await _add_org(db)).id
        sol_a = await _add_solution(db, org)
        sol_b = await _add_solution(db, org)
        # Only install B ships workflows/main.py; install A does not.
        await _add_workflow(db, org_id=org, solution_id=sol_b.id, path="workflows/main.py")

        repo = WorkflowRepository(db, org_id=org, is_superuser=True)
        # Caller scoped to install A: no own match, no _repo/ row → None, NOT B's.
        got = await repo.resolve("workflows/main.py::main", solution_scope=sol_a.id)
        assert got is None

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
