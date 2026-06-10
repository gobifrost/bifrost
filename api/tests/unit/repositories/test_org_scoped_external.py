"""
Unit tests for external-user isolation in OrgScopedRepository (EXT-1).

Semantics under test, for an EXTERNAL, NON-BYPASS principal
(``is_external=True`` and ``is_superuser=False``):

1. No global tier: the name-cascade in ``get()`` and the cascade union in
   ``list()`` exclude ``organization_id IS NULL`` rows.
2. No authenticated-tier entitlement: ``access_level="authenticated"``
   entities do NOT grant — externals need role_based + an assigned role,
   or ownership.
3. By-id access: a global entity is NOT "in scope" for an external user.
4. Bypass unaffected: ``is_superuser=True`` neutralizes the external
   restriction entirely (the provider-org half of bypass is neutralized
   upstream, at token mint — see shared/external_access.py).
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models import Workflow
from src.models.orm.workflow_roles import WorkflowRole
from src.repositories.org_scoped import OrgScopedRepository


class _FakeRepo(OrgScopedRepository[Workflow]):
    """Role-table-less repository (cascade-only semantics, like Table/Config)."""

    model = Workflow


class _FakeRbacRepo(OrgScopedRepository[Workflow]):
    """RBAC repository (access_level semantics, like Form/App/Agent/Workflow)."""

    model = Workflow
    role_table = WorkflowRole
    role_entity_id_column = "workflow_id"


@pytest.fixture
def session():
    s = AsyncMock()
    s.execute = AsyncMock()
    return s


def _entity(org_id, access_level="authenticated", owner_user_id=None):
    e = MagicMock(spec=["id", "organization_id", "access_level", "owner_user_id"])
    e.id = uuid4()
    e.organization_id = org_id
    e.access_level = access_level
    e.owner_user_id = owner_user_id
    return e


def _result_for(entity):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=entity)
    return result


def _rows_result(values):
    """Result shape for queries consumed via .scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


class TestByIdGlobalDenial:
    """get(id=...) must not leak global (NULL-org) entities to externals."""

    async def test_external_user_denied_global_entity_by_id(self, session):
        entity = _entity(org_id=None)
        session.execute.return_value = _result_for(entity)
        repo = _FakeRepo(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        assert await repo.get(id=entity.id) is None

    async def test_regular_user_still_gets_global_entity_by_id(self, session):
        entity = _entity(org_id=None)
        session.execute.return_value = _result_for(entity)
        repo = _FakeRepo(session, org_id=uuid4(), user_id=uuid4())
        assert await repo.get(id=entity.id) is entity

    async def test_external_user_gets_own_org_entity_by_id(self, session):
        org_id = uuid4()
        entity = _entity(org_id=org_id)
        session.execute.return_value = _result_for(entity)
        repo = _FakeRepo(session, org_id=org_id, user_id=uuid4(), is_external=True)
        assert await repo.get(id=entity.id) is entity

    async def test_external_superuser_bypasses_restriction_by_id(self, session):
        entity = _entity(org_id=None)
        session.execute.return_value = _result_for(entity)
        repo = _FakeRepo(
            session,
            org_id=uuid4(),
            user_id=uuid4(),
            is_superuser=True,
            is_external=True,
        )
        assert await repo.get(id=entity.id) is entity


class TestNameCascadeNoGlobalFallback:
    """get(name=...) cascade: no global fallback step for externals."""

    async def test_external_user_org_miss_does_not_fall_back_to_global(self, session):
        # Org-specific lookup misses; the global fallback would hit.
        global_entity = _entity(org_id=None)
        session.execute.side_effect = [_result_for(None), _result_for(global_entity)]
        repo = _FakeRepo(session, org_id=uuid4(), user_id=uuid4(), is_external=True)

        assert await repo.get(name="anything") is None
        # Only the org-specific query ran; the global fallback was skipped.
        assert session.execute.await_count == 1

    async def test_regular_user_org_miss_falls_back_to_global(self, session):
        global_entity = _entity(org_id=None)
        session.execute.side_effect = [_result_for(None), _result_for(global_entity)]
        repo = _FakeRepo(session, org_id=uuid4(), user_id=uuid4())

        assert await repo.get(name="anything") is global_entity

    async def test_external_user_still_resolves_org_specific_by_name(self, session):
        org_id = uuid4()
        entity = _entity(org_id=org_id)
        session.execute.return_value = _result_for(entity)
        repo = _FakeRepo(session, org_id=org_id, user_id=uuid4(), is_external=True)

        assert await repo.get(name="anything") is entity

    async def test_external_superuser_keeps_global_fallback(self, session):
        global_entity = _entity(org_id=None)
        session.execute.side_effect = [_result_for(None), _result_for(global_entity)]
        repo = _FakeRepo(
            session,
            org_id=uuid4(),
            user_id=uuid4(),
            is_superuser=True,
            is_external=True,
        )
        assert await repo.get(name="anything") is global_entity


class TestCascadeScopeSQL:
    """_apply_cascade_scope: the list() union drops the NULL-org tier."""

    def _compiled_where(self, repo):
        from sqlalchemy import select

        query = repo._apply_cascade_scope(select(Workflow))
        return str(query.compile(compile_kwargs={"literal_binds": True}))

    def test_regular_user_union_includes_global_tier(self, session):
        repo = _FakeRepo(session, org_id=uuid4(), user_id=uuid4())
        sql = self._compiled_where(repo)
        assert "organization_id IS NULL" in sql

    def test_external_user_union_excludes_global_tier(self, session):
        repo = _FakeRepo(session, org_id=uuid4(), user_id=uuid4(), is_external=True)
        sql = self._compiled_where(repo)
        assert "organization_id IS NULL" not in sql

    def test_external_superuser_union_keeps_global_tier(self, session):
        repo = _FakeRepo(
            session,
            org_id=uuid4(),
            user_id=uuid4(),
            is_superuser=True,
            is_external=True,
        )
        sql = self._compiled_where(repo)
        assert "organization_id IS NULL" in sql

    def test_external_user_with_no_org_sees_nothing(self, session):
        # Defense-in-depth: a non-superuser principal always has an org
        # (token parsing enforces it), but if an external repo is ever
        # constructed without one it must not fall into global-only scope.
        repo = _FakeRepo(session, org_id=None, user_id=uuid4(), is_external=True)
        sql = self._compiled_where(repo)
        assert "organization_id IS NULL" not in sql
        assert "false" in sql.lower()


class TestAuthenticatedTierDenial:
    """access_level='authenticated' does not grant to external users."""

    async def test_external_user_denied_authenticated_entity(self, session):
        entity = _entity(org_id=uuid4(), access_level="authenticated")
        repo = _FakeRbacRepo(
            session, org_id=entity.organization_id, user_id=uuid4(), is_external=True
        )
        assert await repo._can_access_entity(entity) is False

    async def test_regular_user_granted_authenticated_entity(self, session):
        entity = _entity(org_id=uuid4(), access_level="authenticated")
        repo = _FakeRbacRepo(
            session, org_id=entity.organization_id, user_id=uuid4()
        )
        assert await repo._can_access_entity(entity) is True

    async def test_external_owner_granted_authenticated_entity(self, session):
        user_id = uuid4()
        entity = _entity(
            org_id=uuid4(), access_level="authenticated", owner_user_id=user_id
        )
        repo = _FakeRbacRepo(
            session, org_id=entity.organization_id, user_id=user_id, is_external=True
        )
        assert await repo._can_access_entity(entity) is True

    async def test_external_user_denied_default_access_level(self, session):
        # access_level=None defaults to authenticated — same denial applies.
        entity = _entity(org_id=uuid4(), access_level=None)
        repo = _FakeRbacRepo(
            session, org_id=entity.organization_id, user_id=uuid4(), is_external=True
        )
        assert await repo._can_access_entity(entity) is False

    async def test_external_user_granted_role_based_entity_with_role(self, session):
        role_id = uuid4()
        user_id = uuid4()
        entity = _entity(org_id=uuid4(), access_level="role_based")
        # First query: user's role ids. Second: entity's role ids.
        session.execute.side_effect = [
            _rows_result([role_id]),
            _rows_result([role_id]),
        ]
        repo = _FakeRbacRepo(
            session, org_id=entity.organization_id, user_id=user_id, is_external=True
        )
        assert await repo._can_access_entity(entity) is True

    async def test_external_user_denied_role_based_entity_without_role(self, session):
        entity = _entity(org_id=uuid4(), access_level="role_based")
        session.execute.side_effect = [
            _rows_result([uuid4()]),  # user has a role...
            _rows_result([uuid4()]),  # ...but not one assigned to the entity
        ]
        repo = _FakeRbacRepo(
            session, org_id=entity.organization_id, user_id=uuid4(), is_external=True
        )
        assert await repo._can_access_entity(entity) is False

    async def test_external_superuser_granted_authenticated_entity(self, session):
        entity = _entity(org_id=uuid4(), access_level="authenticated")
        repo = _FakeRbacRepo(
            session,
            org_id=entity.organization_id,
            user_id=uuid4(),
            is_superuser=True,
            is_external=True,
        )
        assert await repo._can_access_entity(entity) is True
