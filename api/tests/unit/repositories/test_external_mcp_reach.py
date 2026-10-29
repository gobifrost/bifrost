"""Authorization SQL seams for shared external-MCP resources."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.repositories.external_mcp import MCPConnectionRepository, MCPServerRepository
from src.services.authorization.reach import NOWHERE, OrgReach


@pytest.mark.asyncio
async def test_connection_list_applies_empty_reach_before_rows_load() -> None:
    """An empty reach becomes a false SQL predicate before result loading."""
    result = MagicMock()
    result.scalars.return_value.unique.return_value.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    await MCPConnectionRepository(session, org_id=None).list_connections(reach=NOWHERE)

    statement = session.execute.await_args.args[0]
    assert "false" in str(statement.compile(compile_kwargs={"literal_binds": True})).lower()


@pytest.mark.asyncio
async def test_template_list_keeps_global_as_selected_org_shared_default() -> None:
    """A permitted organization list can include its shared Global templates."""
    result = MagicMock()
    result.scalars.return_value.unique.return_value.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    organization_id = uuid4()

    await MCPServerRepository(session, org_id=None).list_servers(
        OrgReach(organization_ids=frozenset({organization_id})),
        selected_organization_id=organization_id,
    )

    statement = session.execute.await_args.args[0]
    assert "mcp_servers.organization_id IS NULL" in str(statement)
