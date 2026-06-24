"""Codex #16 (security): the X-Bifrost-App table resolution is client-supplied,
so it must be GATED to the caller's org scope — a caller passing a FOREIGN org's
app id must NOT reach that org's install table by name. A non-superuser only
resolves a table whose org is its own or global; a superuser is unrestricted.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.services.solution_scope import resolve_solution_table_by_name

pytestmark = pytest.mark.e2e


async def _install_with_app_and_table(db, org_id, table_name):
    """A solution install (org-scoped) with one app + one named table."""
    sol = Solution(id=uuid4(), slug=f"s-{uuid4().hex[:8]}", name="S", organization_id=org_id)
    db.add(sol)
    await db.flush()
    app = Application(
        id=uuid4(), name="app", slug=f"app-{uuid4().hex[:8]}", repo_path=f"apps/{uuid4().hex}",
        organization_id=org_id, solution_id=sol.id, app_model="standalone_v2",
    )
    db.add(app)
    table = Table(
        id=uuid4(), name=table_name, organization_id=org_id, solution_id=sol.id,
        schema={"columns": []}, access={"policies": []},
    )
    db.add(table)
    await db.flush()
    return app, table


def _ctx(db, *, app_id, is_superuser) -> Any:
    # Duck-typed Context (matches what _resolve_solution_table_by_name reads).
    # solution_id=None: these tests drive the APP-id arm of the resolver; the
    # ?solution= arm (F2 chokepoint) reads ctx.solution_id first.
    return cast(Any, SimpleNamespace(
        db=db, app_id=str(app_id), solution_id=None,
        user=SimpleNamespace(
            user_id=uuid4(),
            is_superuser=is_superuser,
            is_external=False,
        ),
    ))


async def test_foreign_org_app_id_does_not_resolve_other_orgs_table(db_session):
    """A non-superuser in org A passing org B's app id must NOT resolve org B's
    install table — the org gate blocks it (returns None)."""
    db = db_session
    org_a = Organization(id=uuid4(), name=f"A-{uuid4().hex[:6]}", created_by="t")
    org_b = Organization(id=uuid4(), name=f"B-{uuid4().hex[:6]}", created_by="t")
    db.add_all([org_a, org_b])
    await db.flush()

    name = f"customers_{uuid4().hex[:8]}"
    app_b, table_b = await _install_with_app_and_table(db, org_b.id, name)

    # Caller is a NON-superuser scoped to org A, but supplies org B's app id.
    ctx = _ctx(db, app_id=app_b.id, is_superuser=False)
    got = await resolve_solution_table_by_name(db, ctx, name, target_org_id=org_a.id)
    assert got is None, "cross-tenant table resolved via a foreign app_id header"


async def test_own_org_app_id_resolves_its_table(db_session):
    """The legitimate case still works: a caller in org B with org B's app id
    resolves org B's install table by name."""
    db = db_session
    org_b = Organization(id=uuid4(), name=f"B-{uuid4().hex[:6]}", created_by="t")
    db.add(org_b)
    await db.flush()
    name = f"customers_{uuid4().hex[:8]}"
    app_b, table_b = await _install_with_app_and_table(db, org_b.id, name)

    ctx = _ctx(db, app_id=app_b.id, is_superuser=False)
    got = await resolve_solution_table_by_name(db, ctx, name, target_org_id=org_b.id)
    assert got is not None and got.id == table_b.id


async def test_global_install_table_resolves_for_any_org(db_session):
    """A GLOBAL solution table (organization_id NULL) is visible to any caller."""
    db = db_session
    org_a = Organization(id=uuid4(), name=f"A-{uuid4().hex[:6]}", created_by="t")
    db.add(org_a)
    await db.flush()
    name = f"shared_{uuid4().hex[:8]}"
    app_g, table_g = await _install_with_app_and_table(db, None, name)  # global install

    ctx = _ctx(db, app_id=app_g.id, is_superuser=False)
    got = await resolve_solution_table_by_name(db, ctx, name, target_org_id=org_a.id)
    assert got is not None and got.id == table_g.id
