"""
LEAK #2 failing-first proof: MCPToolAccessService._get_accessible_agents
queried agents across ALL orgs with a role-NAME match and no org filter, so a
role_based agent in another org (or global) with a same-named role leaked to
the caller. The org cascade must be applied in-query. The cascade is the same
for every principal (org + global) — external access is governed by the
access-level filter, not by scope.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.mcp_server.tool_access import MCPToolAccessService


@pytest.fixture
def session():
    s = AsyncMock()
    s.execute = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.unique.return_value.all.return_value = []
    s.execute.return_value = result
    return s


def _executed_sql(session) -> str:
    """Concatenated WHERE clauses of every executed statement (the column list
    always names organization_id, so inspect only the predicate)."""
    out = []
    for call in session.execute.await_args_list:
        stmt = call.args[0]
        try:
            full = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        except Exception:
            full = str(stmt)
        _, _, where = full.partition("WHERE")
        out.append(where)
    return "\n".join(out)


@pytest.mark.asyncio
class TestAccessibleAgentsOrgScope:
    async def test_regular_user_query_is_org_scoped(self, session):
        svc = MCPToolAccessService(session)
        org = uuid4()
        await svc._get_accessible_agents(
            user_roles=["r"], is_superuser=False, org_id=org
        )
        sql = _executed_sql(session)
        # Org cascade present: own org AND global arm.
        assert "organization_id" in sql
        assert "IS NULL" in sql

    async def test_external_user_query_keeps_normal_cascade(self, session):
        svc = MCPToolAccessService(session)
        org = uuid4()
        await svc._get_accessible_agents(
            user_roles=["r"], is_superuser=False, is_external=True, org_id=org
        )
        sql = _executed_sql(session)
        # Same cascade as any org user — global role_based agents an external
        # was granted must remain reachable; the access-level filter (not the
        # scope) is what excludes authenticated-tier agents.
        assert "organization_id" in sql
        assert "IS NULL" in sql

    async def test_superuser_query_unscoped(self, session):
        svc = MCPToolAccessService(session)
        await svc._get_accessible_agents(
            user_roles=[], is_superuser=True, org_id=uuid4()
        )
        sql = _executed_sql(session)
        # Superuser: no org filter (full visibility).
        assert "organization_id" not in sql


@pytest.mark.asyncio
class TestGetToolsForAgentByIdOrgScope:
    """get_tools_for_agent fetches a specific agent by id — that fetch must be
    org-scoped IN THE DATABASE, else a cross-org/global role_based agent with a
    same-named role passes _check_agent_access (LEAK #2 by-id variant)."""

    async def test_regular_user_by_id_query_is_org_scoped(self, session):
        svc = MCPToolAccessService(session)
        await svc.get_tools_for_agent(
            agent_id=str(uuid4()), user_roles=["r"], is_superuser=False,
            org_id=uuid4(),
        )
        sql = _executed_sql(session)
        assert "organization_id" in sql
        assert "IS NULL" in sql

    async def test_external_user_by_id_query_keeps_normal_cascade(self, session):
        svc = MCPToolAccessService(session)
        await svc.get_tools_for_agent(
            agent_id=str(uuid4()), user_roles=["r"], is_superuser=False,
            is_external=True, org_id=uuid4(),
        )
        sql = _executed_sql(session)
        assert "organization_id" in sql
        assert "IS NULL" in sql
