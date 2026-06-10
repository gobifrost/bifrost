"""Websocket table-name resolution must apply the canonical solution/orphan filters.

A `_repo/` table and a solution-deployed table may legally share (org, name) —
that's this branch's own design. The websocket name branch of
`_resolve_table_id` (and `_load_policies_for_table`) previously selected by
bare name, so the duplicate raised sqlalchemy MultipleResultsFound, which
propagated to the connection-level handler and killed the ENTIRE websocket.

Canonical semantics (mirror `OrgScopedRepository.get()`): by-name resolution is
the LIVE `_repo/` namespace — solution-managed rows resolve by id (or
?solution=), orphaned rows don't resolve by name at all.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from src.core.auth import UserPrincipal
from src.models.contracts.policies import TablePolicies
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.routers import websocket as ws_mod

pytestmark = pytest.mark.e2e


@pytest.fixture
def patched_db(monkeypatch, db_session):
    """Route the websocket module's `get_db_context` to the test session.

    The helpers under test open their own session via `get_db_context`;
    pointing it at the (uncommitted, rolled-back) test session keeps seeded
    rows visible to them without committing anything.
    """

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(ws_mod, "get_db_context", _ctx)
    return db_session


async def _make_org(db) -> uuid.UUID:
    org = Organization(id=uuid.uuid4(), name=f"Org-{uuid.uuid4().hex[:8]}", created_by="dev@x")
    db.add(org)
    await db.flush()
    return org.id


async def _make_solution(db, org_id) -> uuid.UUID:
    solution = Solution(
        id=uuid.uuid4(),
        slug=f"sol-{uuid.uuid4().hex[:8]}",
        name="Test Solution",
        organization_id=org_id,
    )
    db.add(solution)
    await db.flush()
    return solution.id


def _table(name: str, *, org_id, solution_id=None, orphaned: bool = False, access=None) -> Table:
    return Table(
        id=uuid.uuid4(),
        name=name,
        organization_id=org_id,
        solution_id=solution_id,
        origin_solution_slug="x" if orphaned else None,
        origin_solution_id=uuid.uuid4() if orphaned else None,
        orphaned_at=datetime.now(timezone.utc) if orphaned else None,
        created_by="dev@x",
        access=access,
    )


def _org_user(org_id) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid.uuid4(),
        email="user@x",
        organization_id=org_id,
        is_superuser=False,
    )


class TestResolveTableIdByName:
    async def test_plain_repo_table_still_resolves(self, patched_db) -> None:
        """Happy-path regression guard: an ordinary `_repo/` table with no
        same-name siblings must keep resolving by name — an over-aggressive
        future filter change should fail here, not in production.
        """
        db = patched_db
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        live = _table(name, org_id=org)
        db.add(live)
        await db.flush()

        assert await ws_mod._resolve_table_id(name, _org_user(org)) == str(live.id)

    async def test_repo_row_wins_over_same_name_solution_row(self, patched_db) -> None:
        """A live `_repo/` table and a solution table share (org, name): the
        name lookup must return the `_repo/` row — not raise
        MultipleResultsFound (which killed the whole websocket).
        """
        db = patched_db
        org = await _make_org(db)
        solution_id = await _make_solution(db, org)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        live = _table(name, org_id=org)
        managed = _table(name, org_id=org, solution_id=solution_id)
        db.add_all([live, managed])
        await db.flush()

        resolved = await ws_mod._resolve_table_id(name, _org_user(org))
        assert resolved == str(live.id)

    async def test_orphan_only_name_resolves_to_none(self, patched_db) -> None:
        """Orphaned rows don't resolve by name at all — if the only match is
        orphaned, the lookup returns None (table-not-found), never the orphan.
        """
        db = patched_db
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        db.add(_table(name, org_id=org, orphaned=True))
        await db.flush()

        assert await ws_mod._resolve_table_id(name, _org_user(org)) is None


class TestLoadPoliciesForTableByName:
    async def test_name_lookup_skips_solution_rows(self, patched_db) -> None:
        """Same-name `_repo/` + solution rows: the name branch must load the
        `_repo/` row's policies (empty here), not raise MultipleResultsFound
        and not read the solution row's policy document.
        """
        db = patched_db
        org = await _make_org(db)
        solution_id = await _make_solution(db, org)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        live = _table(name, org_id=org, access=None)
        managed = _table(
            name,
            org_id=org,
            solution_id=solution_id,
            access={"policies": [{"name": "deny-all-marker", "actions": ["read"]}]},
        )
        db.add_all([live, managed])
        await db.flush()

        policies = await ws_mod._load_policies_for_table(name)
        assert policies == TablePolicies()

    async def test_name_lookup_excludes_orphans(self, patched_db) -> None:
        """Orphan-only name: the policy name branch returns None (not-found),
        never the orphaned row's policy document.
        """
        db = patched_db
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        db.add(_table(name, org_id=org, orphaned=True))
        await db.flush()

        assert await ws_mod._load_policies_for_table(name) is None
