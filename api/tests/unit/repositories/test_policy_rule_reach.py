"""Authorization SQL seams for policy-rule enumeration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.repositories.policy_rule import PolicyRuleRepository
from src.services.authorization.reach import NOWHERE


@pytest.mark.asyncio
async def test_list_applies_empty_reach_in_sql_before_loading_rows() -> None:
    """An empty reach must become a SQL false predicate, never a row post-filter."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    repo = PolicyRuleRepository(session, org_id=None, is_superuser=True)

    await repo.list(reach=NOWHERE)

    statement = session.execute.await_args.args[0]
    assert "false" in str(statement.compile(compile_kwargs={"literal_binds": True})).lower()
