"""MCP agent queries use the current Caller reach in the SQL predicate."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.mcp_server.tool_access import MCPToolAccessService
from tests.helpers.authorization import admin_caller


@pytest.fixture
def session():
    value = AsyncMock()
    value.execute = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.unique.return_value.all.return_value = []
    value.execute.return_value = result
    return value


def _caller(*, external: bool = False) -> Caller:
    org_id = uuid4()
    principal = UserPrincipal(
        user_id=uuid4(),
        email="agent-user@example.test",
        organization_id=org_id,
        is_external=external,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=org_id,
            base_role_id=uuid4(),
            is_external=external,
            base_permissions=frozenset(),
            role_grants=(),
        ),
    )


def _executed_sql(session) -> str:
    """Inspect WHERE clauses only; selected columns always include org id."""
    clauses = []
    for call in session.execute.await_args_list:
        statement = call.args[0]
        try:
            full = str(statement.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            full = str(statement)
        _, _, where = full.partition("WHERE")
        clauses.append(where)
    return "\n".join(clauses)


@pytest.mark.asyncio
class TestAccessibleAgentsOrgScope:
    async def test_regular_user_query_is_org_scoped(self, session):
        await MCPToolAccessService(session)._get_accessible_agents(_caller())
        sql = _executed_sql(session)
        assert "organization_id" in sql
        assert "IS NULL" in sql

    async def test_external_user_query_keeps_normal_cascade(self, session):
        await MCPToolAccessService(session)._get_accessible_agents(_caller(external=True))
        sql = _executed_sql(session)
        assert "organization_id" in sql
        assert "IS NULL" in sql

    async def test_platform_admin_query_is_unscoped(self, session):
        await MCPToolAccessService(session)._get_accessible_agents(admin_caller())
        assert "organization_id" not in _executed_sql(session)


@pytest.mark.asyncio
class TestGetToolsForAgentByIdOrgScope:
    async def test_regular_user_by_id_query_is_org_scoped(self, session):
        await MCPToolAccessService(session).get_tools_for_agent(uuid4(), _caller())
        sql = _executed_sql(session)
        assert "organization_id" in sql
        assert "IS NULL" in sql

    async def test_external_user_by_id_query_keeps_normal_cascade(self, session):
        await MCPToolAccessService(session).get_tools_for_agent(
            uuid4(), _caller(external=True)
        )
        sql = _executed_sql(session)
        assert "organization_id" in sql
        assert "IS NULL" in sql
