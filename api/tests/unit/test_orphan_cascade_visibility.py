"""Orphaned rows must not leak into the normal org name cascade.

When a Solution install is uninstalled non-destructively, its owned tables are
ORPHANED to preserve data: solution_id is NULL'd (so the row survives) and
provenance columns (origin_solution_slug/origin_solution_id/orphaned_at) are
stamped (orphaned_at IS NOT NULL ⇔ orphaned). An orphaned table therefore has
solution_id IS NULL — so the existing `solution_id IS NULL` cascade filter no
longer excludes it. Without an extra orphaned_at filter it would LEAK into the
normal org name cascade: a regular workflow doing get(name="...") could resolve
a former install's orphaned table and read its data (cross-context leak).

These tests assert the name-cascade get() path excludes orphaned rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.tables import Table
from src.repositories.tables import TableRepository

pytestmark = pytest.mark.e2e


async def _make_org(db) -> uuid.UUID:
    org = Organization(id=uuid.uuid4(), name=f"Org-{uuid.uuid4().hex[:8]}", created_by="dev@x")
    db.add(org)
    await db.flush()
    return org.id


def _table(name: str, *, org_id, solution_id=None, orphaned: bool = False) -> Table:
    return Table(
        id=uuid.uuid4(),
        name=name,
        organization_id=org_id,
        solution_id=solution_id,
        origin_solution_slug="x" if orphaned else None,
        origin_solution_id=uuid.uuid4() if orphaned else None,
        orphaned_at=datetime.now(timezone.utc) if orphaned else None,
        created_by="dev@x",
        access=None,
    )


class TestOrphanCascadeVisibility:
    async def test_orphaned_global_does_not_leak_into_org_cascade(self, db_session) -> None:
        """An org-scoped normal table and a GLOBAL orphaned table share a name.
        (Orphaned rows are excluded from the live-name unique indexes — a
        normal+orphan pair may share a name even in the SAME scope — but the
        cross-context case worth guarding is org-normal vs global-orphan, since
        the global row also sits in the org's cascade fallback.)

        get(name=) for the org returns the NORMAL org table; the orphan must never
        win, and must not leak even if the org table were absent (see next test).
        """
        db = db_session
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        normal = _table(name, org_id=org)
        global_orphan = _table(name, org_id=None, orphaned=True)
        db.add_all([normal, global_orphan])
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        got = await repo.get(name=name)
        assert got is not None
        assert got.id == normal.id
        assert got.orphaned_at is None, "name cascade must resolve the non-orphaned table"

    async def test_orphan_excluded_even_when_only_match(self, db_session) -> None:
        """If the ONLY table of that name is orphaned, get(name=) returns None
        (the orphan is invisible to the cascade), NOT the orphan.
        """
        db = db_session
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        orphan = _table(name, org_id=org, orphaned=True)
        db.add(orphan)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        assert await repo.get(name=name) is None

    async def test_global_orphan_excluded_from_org_fallback(self, db_session) -> None:
        """A GLOBAL orphan must not leak into an org's cascade via the global
        fallback step when no org table of that name exists."""
        db = db_session
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        global_orphan = _table(name, org_id=None, orphaned=True)
        db.add(global_orphan)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        assert await repo.get(name=name) is None

    async def test_normal_table_still_resolves(self, db_session) -> None:
        """Regression: a genuinely non-solution table still resolves (no over-exclusion)."""
        db = db_session
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        normal = _table(name, org_id=org)
        db.add(normal)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        got = await repo.get(name=name)
        assert got is not None and got.id == normal.id

    async def test_solution_managed_table_still_excluded(self, db_session) -> None:
        """Regression: a solution-MANAGED table (solution_id set) stays excluded
        from the name cascade as before."""
        from src.models.orm.solutions import Solution

        db = db_session
        org = await _make_org(db)
        sol = Solution(
            id=uuid.uuid4(),
            slug=f"oc-{uuid.uuid4().hex[:8]}",
            name="OC",
            organization_id=None,
        )
        db.add(sol)
        await db.flush()

        name = f"customers_{uuid.uuid4().hex[:8]}"
        managed = _table(name, org_id=org, solution_id=sol.id)
        db.add(managed)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        assert await repo.get(name=name) is None

    async def test_orphan_still_fetchable_by_id(self, db_session) -> None:
        """The id path is untouched: an orphan is still fetchable BY ID (for the
        show-orphaned / delete path)."""
        db = db_session
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        orphan = _table(name, org_id=org, orphaned=True)
        db.add(orphan)
        await db.flush()

        repo = TableRepository(session=db, org_id=org, user_id=None, is_superuser=True)
        got = await repo.get(id=orphan.id)
        assert got is not None and got.id == orphan.id
