"""SPIKE: per-call solution= resolves slug/name inside the resolved scope."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.solution_scope import (
    derive_execution_solution_scope,
    resolve_solution_ref,
)


async def _org(db):
    o = Organization(id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="test")
    db.add(o)
    await db.flush()
    return o


async def _sol(db, org_id, slug):
    s = Solution(id=uuid4(), slug=slug, name=f"Name-{slug}", organization_id=org_id)
    db.add(s)
    await db.flush()
    return s


def _no_ctx():
    return SimpleNamespace(solution_id=None, app_id=None)


@pytest.mark.e2e
class TestSpikeSolutionRef:
    async def test_slug_resolves_in_scope(self, db_session):
        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "acme-crm")
        got = await resolve_solution_ref(db_session, "acme-crm", org)
        assert got == sol.id

    async def test_slug_does_not_cross_org(self, db_session):
        org_a = (await _org(db_session)).id
        org_b = (await _org(db_session)).id
        await _sol(db_session, org_a, "acme-crm")
        got = await resolve_solution_ref(db_session, "acme-crm", org_b)
        assert got is None

    async def test_derive_body_slug_wins_when_ctx_unset(self, db_session):
        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "acme-crm")
        got = await derive_execution_solution_scope(
            db_session, _no_ctx(), solution_id="acme-crm", form_id=None, app_id=None,
            target_org_id=org,
        )
        assert got == sol.id

    async def test_uuid_passthrough_unchanged(self, db_session):
        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "acme-crm")
        got = await derive_execution_solution_scope(
            db_session, _no_ctx(), solution_id=str(sol.id), form_id=None, app_id=None,
            target_org_id=org,
        )
        assert got == sol.id

    async def test_sealed_install_denies_cross_caller(self, db_session):
        from src.services.solution_scope import check_inbound_allowed

        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "sealed")
        sol.allow_inbound_access = False
        await db_session.flush()
        # Outside caller (None) denied…
        assert await check_inbound_allowed(db_session, sol.id, None) is False
        # …own-install caller always passes.
        assert await check_inbound_allowed(db_session, sol.id, sol.id) is True

    async def test_open_install_allows_cross_caller(self, db_session):
        from src.services.solution_scope import check_inbound_allowed

        org = (await _org(db_session)).id
        sol = await _sol(db_session, org, "open")
        assert await check_inbound_allowed(db_session, sol.id, None) is True
