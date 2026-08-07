from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.auth import ExecutionContext
from src.models.contracts.policies import TablePolicies
from src.models.contracts.tables import TableUpdate
from src.models.orm.tables import Table
from src.routers import tables


@pytest.mark.asyncio
async def test_policy_update_commits_before_notifying_subscribers(
    db_session,
    monkeypatch,
):
    table = Table(
        name=f"policy_publish_{uuid4().hex[:8]}",
        organization_id=None,
        access={"policies": []},
    )
    db_session.add(table)
    await db_session.commit()

    events: list[str] = []
    original_commit = db_session.commit

    async def tracked_commit() -> None:
        await original_commit()
        events.append("commit")

    async def tracked_publish(table_id: str) -> None:
        assert table_id == str(table.id)
        events.append("publish")

    monkeypatch.setattr(db_session, "commit", tracked_commit)
    monkeypatch.setattr(tables, "publish_policy_changed", tracked_publish)
    monkeypatch.setattr(
        tables,
        "assert_entity_id_not_solution_managed",
        AsyncMock(),
    )

    ctx = ExecutionContext(user=None, org_id=None, db=db_session)  # type: ignore[arg-type]
    await tables.update_table(
        table.id,
        TableUpdate(policies=TablePolicies(policies=[])),
        ctx,
        None,  # type: ignore[arg-type]
    )

    assert events == ["commit", "publish"]
