"""Exact Solution resource resolution shared by HTTP and WebSocket runtimes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.app_actor import SolutionAppPrincipal
from src.services.builder.app_scope import resolve_solution_table


def _principal() -> SolutionAppPrincipal:
    return SolutionAppPrincipal(
        actor_user_id=uuid4(),
        solution_id=uuid4(),
        app_id=uuid4(),
        organization_id=uuid4(),
        jti="runtime-session",
        scopes=frozenset(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup_by_id", [False, True])
async def test_solution_table_resolves_name_or_uuid_with_exact_solution_scope(
    lookup_by_id: bool,
) -> None:
    principal = _principal()
    table_id = uuid4()
    table = SimpleNamespace(id=table_id)
    result = MagicMock()
    result.scalar_one_or_none.return_value = table
    db = AsyncMock()
    db.execute.return_value = result

    lookup = str(table_id) if lookup_by_id else "expense-items"
    resolved = await resolve_solution_table(db, principal, lookup)

    assert resolved is table
    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    assert principal.solution_id in compiled.params.values()
    assert (table_id if lookup_by_id else lookup) in compiled.params.values()
    where_sql = str(statement.whereclause)
    assert ("tables.id" if lookup_by_id else "tables.name") in where_sql
