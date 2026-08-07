"""Deployed (solution-managed) entities must be VISIBLE in list() — criterion 16
says the Solution object is invisible, not the entities it deploys. But name/path
cascade get() must still resolve the _repo/ entity (solution entities resolve by
id at execution, never by name cascade), so a _repo/ name and a solution name can
coexist without MultipleResultsFound.

Regression guard for the org_scoped.py fix: the `solution_id IS NULL` filter
belongs on the name-cascade get() path, NOT on list().
"""
from __future__ import annotations

import uuid

import pytest

from src.models import Workflow
from src.models.orm.solutions import Solution
from src.repositories.org_scoped import OrgScopedRepository

pytestmark = pytest.mark.e2e


class _WfRepo(OrgScopedRepository[Workflow]):
    model = Workflow


def _wf(name: str, *, org_id, solution_id=None) -> Workflow:
    return Workflow(
        id=uuid.uuid4(),
        name=name,
        function_name="run",
        path=f"workflows/{name}.py",
        type="workflow",
        is_active=True,
        organization_id=org_id,
        solution_id=solution_id,
    )


async def test_list_includes_solution_managed_entities(db_session):
    db = db_session
    sol = Solution(id=uuid.uuid4(), slug=f"vis-{uuid.uuid4().hex[:8]}", name="VIS", organization_id=None)
    db.add(sol)
    await db.flush()

    uniq = uuid.uuid4().hex[:8]
    repo_wf = _wf(f"repo_{uniq}", org_id=None)
    sol_wf = _wf(f"deployed_{uniq}", org_id=None, solution_id=sol.id)
    db.add_all([repo_wf, sol_wf])
    await db.flush()

    repo = _WfRepo(session=db, org_id=None, user_id=None, is_superuser=True)
    names = {w.name for w in await repo.list()}
    # The deployed (solution-managed) workflow MUST appear in the listing.
    assert f"deployed_{uniq}" in names, "solution-managed entity hidden from list()"
    assert f"repo_{uniq}" in names


async def test_get_by_name_resolves_repo_not_solution(db_session):
    """A _repo/ workflow and a solution workflow can share a name; get(name=)
    resolves the _repo/ one without MultipleResultsFound."""
    db = db_session
    sol = Solution(id=uuid.uuid4(), slug=f"vis2-{uuid.uuid4().hex[:8]}", name="VIS2", organization_id=None)
    db.add(sol)
    await db.flush()

    shared = f"shared_{uuid.uuid4().hex[:8]}"
    repo_wf = _wf(shared, org_id=None)
    sol_wf = _wf(shared, org_id=None, solution_id=sol.id)
    db.add_all([repo_wf, sol_wf])
    await db.flush()

    repo = _WfRepo(session=db, org_id=None, user_id=None, is_superuser=True)
    got = await repo.get(name=shared)  # must NOT raise MultipleResultsFound
    assert got is not None
    assert got.solution_id is None, "name cascade must resolve the _repo/ entity, not the solution one"


async def test_app_slug_open_finds_managed(db_session):
    """A deployed (solution-managed) app must be openable by slug via
    can_access(slug=, include_solution_managed=True) (criterion 16, Codex P1-b)."""
    from src.models.orm.applications import Application
    from src.repositories.applications import ApplicationRepository

    db = db_session
    sol = Solution(id=uuid.uuid4(), slug=f"appvis-{uuid.uuid4().hex[:8]}", name="AV", organization_id=None)
    db.add(sol)
    await db.flush()
    slug = f"dash-{uuid.uuid4().hex[:8]}"
    app_id = uuid.uuid4()
    db.add(Application(
        id=app_id, name="Dash", slug=slug, repo_path=f"apps/{slug}",
        organization_id=None, solution_id=sol.id,
    ))
    await db.flush()

    repo = ApplicationRepository(db, org_id=None, user_id=None, is_superuser=False)
    # Default get(slug=) would exclude managed → None (collision-safe default).
    assert await repo.get(slug=slug) is None
    # With the flag, the managed app resolves.
    found = await repo.can_access(slug=slug, include_solution_managed=True)
    assert found.id == app_id


async def test_admin_slug_resolves_cross_org_without_multipleresults(db_session):
    """The same solution app slug installed for two orgs is legitimate
    (criterion 9). An admin's get_by_slug_global must disambiguate by the active
    org instead of raising MultipleResultsFound (Codex R4)."""
    from src.models.orm.applications import Application
    from src.models.orm.organizations import Organization
    from src.repositories.applications import ApplicationRepository

    db = db_session
    org_a = Organization(id=uuid.uuid4(), name=f"A-{uuid.uuid4().hex[:6]}", created_by="dev@x")
    org_b = Organization(id=uuid.uuid4(), name=f"B-{uuid.uuid4().hex[:6]}", created_by="dev@x")
    db.add_all([org_a, org_b])
    await db.flush()
    # Two SOLUTION installs (the criterion-9 cross-org case): the global
    # _repo/ slug index is partial on solution_id IS NULL, so only
    # solution-managed apps can legitimately share a slug across orgs.
    sol_a = Solution(id=uuid.uuid4(), slug=f"sa-{uuid.uuid4().hex[:6]}", name="SA", organization_id=org_a.id)
    sol_b = Solution(id=uuid.uuid4(), slug=f"sb-{uuid.uuid4().hex[:6]}", name="SB", organization_id=org_b.id)
    db.add_all([sol_a, sol_b])
    await db.flush()
    slug = f"dash-{uuid.uuid4().hex[:8]}"
    app_a = Application(id=uuid.uuid4(), name="A", slug=slug, repo_path=f"apps/{slug}",
                        organization_id=org_a.id, solution_id=sol_a.id)
    app_b = Application(id=uuid.uuid4(), name="B", slug=slug, repo_path=f"apps/{slug}",
                        organization_id=org_b.id, solution_id=sol_b.id)
    db.add_all([app_a, app_b])
    await db.flush()

    # Admin scoped to org A resolves A's copy (no MultipleResultsFound 500).
    repo_a = ApplicationRepository(db, org_id=org_a.id, user_id=None, is_superuser=True)
    got_a = await repo_a.get_by_slug_global(slug)
    assert got_a is not None and got_a.id == app_a.id
    # Admin scoped to org B resolves B's copy.
    repo_b = ApplicationRepository(db, org_id=org_b.id, user_id=None, is_superuser=True)
    got_b = await repo_b.get_by_slug_global(slug)
    assert got_b is not None and got_b.id == app_b.id
