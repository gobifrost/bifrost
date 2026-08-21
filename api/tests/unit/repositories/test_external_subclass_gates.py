"""
Repository subclasses that hand-roll cascade keep it pure org→global.

``AgentRepository.list_agents`` / ``get_agent_with_access_check`` and
``FormRepository.get_form_with_access_check`` build their own org/global
queries (for eager loading and the private-agent OR-branch) instead of
calling the base cascade primitive. The cascade must be identical for every
principal — ``is_external`` never drops the global (NULL-org) tier; external
access is governed by the access-level check, not scope.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.repositories.agents import AgentRepository
from src.repositories.forms import FormRepository


@pytest.fixture
def session():
    s = AsyncMock()
    s.execute = AsyncMock()
    return s


def _scalar_result(entity):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=entity)
    return result


def _list_result(values):
    result = MagicMock()
    result.scalars.return_value.unique.return_value.all.return_value = values
    result.scalars.return_value.all.return_value = values
    return result


def _compiled(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": True}))


class TestAgentListCascade:
    async def test_external_list_agents_query_includes_global_tier(self, session):
        session.execute.return_value = _list_result([])
        repo = AgentRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        await repo.list_agents()
        sql = _compiled(session.execute.await_args.args[0])
        assert "organization_id IS NULL" in sql

    async def test_regular_list_agents_query_includes_global_tier(self, session):
        session.execute.return_value = _list_result([])
        repo = AgentRepository(session, org_id=uuid4(), user_id=uuid4())
        await repo.list_agents()
        sql = _compiled(session.execute.await_args.args[0])
        assert "organization_id IS NULL" in sql


class TestAgentByIdCascade:
    async def test_external_get_with_access_check_keeps_global_fallback(self, session):
        session.execute.return_value = _scalar_result(None)
        repo = AgentRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        assert await repo.get_agent_with_access_check(uuid4()) is None
        assert session.execute.await_count == 2

    async def test_regular_get_with_access_check_keeps_global_fallback(self, session):
        session.execute.return_value = _scalar_result(None)
        repo = AgentRepository(session, org_id=uuid4(), user_id=uuid4())
        assert await repo.get_agent_with_access_check(uuid4()) is None
        assert session.execute.await_count == 2


class TestAgentRepositoryAdmission:
    async def test_role_based_agent_admits_effective_role(self, session, monkeypatch):
        user_id = uuid4()
        org_id = uuid4()
        role_id = uuid4()
        agent = SimpleNamespace(
            id=uuid4(),
            organization_id=org_id,
            access_level="role_based",
            solution_id=None,
        )
        role_result = MagicMock()
        role_result.scalars.return_value.all.return_value = [role_id]
        session.execute.return_value = role_result
        monkeypatch.setattr(
            "src.repositories.org_scoped.is_private_solution_owner",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "src.repositories.org_scoped.resolve_effective_role_ids",
            AsyncMock(return_value=frozenset({role_id})),
        )

        repo = AgentRepository(
            session,
            org_id=org_id,
            user_id=user_id,
            bypass_resource_roles=False,
        )

        assert await repo._can_access_entity(agent) is True

    async def test_agent_bypass_resource_roles_admits_without_role(
        self,
        session,
        monkeypatch,
    ):
        user_id = uuid4()
        org_id = uuid4()
        agent = SimpleNamespace(
            id=uuid4(),
            organization_id=org_id,
            access_level="role_based",
            solution_id=None,
        )
        resolve_roles = AsyncMock()
        monkeypatch.setattr(
            "src.repositories.org_scoped.is_private_solution_owner",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "src.repositories.org_scoped.resolve_effective_role_ids",
            resolve_roles,
        )

        repo = AgentRepository(
            session,
            org_id=org_id,
            user_id=user_id,
            bypass_resource_roles=True,
        )

        assert await repo._can_access_entity(agent) is True
        resolve_roles.assert_not_awaited()
        session.execute.assert_not_awaited()

    async def test_role_based_agent_denies_without_effective_role(
        self,
        session,
        monkeypatch,
    ):
        user_id = uuid4()
        org_id = uuid4()
        agent_role_id = uuid4()
        effective_role_id = uuid4()
        agent = SimpleNamespace(
            id=uuid4(),
            organization_id=org_id,
            access_level="role_based",
            solution_id=None,
        )
        role_result = MagicMock()
        role_result.scalars.return_value.all.return_value = [agent_role_id]
        session.execute.return_value = role_result
        monkeypatch.setattr(
            "src.repositories.org_scoped.is_private_solution_owner",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "src.repositories.org_scoped.resolve_effective_role_ids",
            AsyncMock(return_value=frozenset({effective_role_id})),
        )

        repo = AgentRepository(
            session,
            org_id=org_id,
            user_id=user_id,
            bypass_resource_roles=False,
        )

        assert await repo._can_access_entity(agent) is False


class TestFormByIdCascade:
    async def test_external_get_with_access_check_keeps_global_fallback(self, session):
        session.execute.return_value = _scalar_result(None)
        repo = FormRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        assert await repo.get_form_with_access_check(uuid4()) is None
        assert session.execute.await_count == 2

    async def test_regular_get_with_access_check_keeps_global_fallback(self, session):
        session.execute.return_value = _scalar_result(None)
        repo = FormRepository(session, org_id=uuid4(), user_id=uuid4())
        assert await repo.get_form_with_access_check(uuid4()) is None
        assert session.execute.await_count == 2


class TestFormRepositoryAdmission:
    async def test_role_based_form_admits_effective_role(self, session, monkeypatch):
        user_id = uuid4()
        org_id = uuid4()
        role_id = uuid4()
        form = SimpleNamespace(
            id=uuid4(),
            organization_id=org_id,
            access_level="role_based",
            solution_id=None,
        )
        role_result = MagicMock()
        role_result.scalars.return_value.all.return_value = [role_id]
        session.execute.return_value = role_result
        monkeypatch.setattr(
            "src.repositories.org_scoped.is_private_solution_owner",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "src.repositories.org_scoped.resolve_effective_role_ids",
            AsyncMock(return_value=frozenset({role_id})),
        )

        repo = FormRepository(
            session,
            org_id=org_id,
            user_id=user_id,
            bypass_resource_roles=False,
        )

        assert await repo._can_access_entity(form) is True

    async def test_form_bypass_resource_roles_admits_without_role(
        self,
        session,
        monkeypatch,
    ):
        user_id = uuid4()
        org_id = uuid4()
        form = SimpleNamespace(
            id=uuid4(),
            organization_id=org_id,
            access_level="role_based",
            solution_id=None,
        )
        resolve_roles = AsyncMock()
        monkeypatch.setattr(
            "src.repositories.org_scoped.is_private_solution_owner",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "src.repositories.org_scoped.resolve_effective_role_ids",
            resolve_roles,
        )

        repo = FormRepository(
            session,
            org_id=org_id,
            user_id=user_id,
            bypass_resource_roles=True,
        )

        assert await repo._can_access_entity(form) is True
        resolve_roles.assert_not_awaited()
        session.execute.assert_not_awaited()

    async def test_role_based_form_denies_without_effective_role(
        self,
        session,
        monkeypatch,
    ):
        user_id = uuid4()
        org_id = uuid4()
        form_role_id = uuid4()
        effective_role_id = uuid4()
        form = SimpleNamespace(
            id=uuid4(),
            organization_id=org_id,
            access_level="role_based",
            solution_id=None,
        )
        role_result = MagicMock()
        role_result.scalars.return_value.all.return_value = [form_role_id]
        session.execute.return_value = role_result
        monkeypatch.setattr(
            "src.repositories.org_scoped.is_private_solution_owner",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "src.repositories.org_scoped.resolve_effective_role_ids",
            AsyncMock(return_value=frozenset({effective_role_id})),
        )

        repo = FormRepository(
            session,
            org_id=org_id,
            user_id=user_id,
            bypass_resource_roles=False,
        )

        assert await repo._can_access_entity(form) is False
