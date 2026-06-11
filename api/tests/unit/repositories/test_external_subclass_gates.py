"""
Repository subclasses that hand-roll cascade keep it pure org→global.

``AgentRepository.list_agents`` / ``get_agent_with_access_check`` and
``FormRepository.get_form_with_access_check`` build their own org/global
queries (for eager loading and the private-agent OR-branch) instead of
calling the base cascade primitive. The cascade must be identical for every
principal — ``is_external`` never drops the global (NULL-org) tier; external
access is governed by the access-level check, not scope.
"""

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
