from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.routers import agent_runs
from src.services.authorization.reach import OrgReach


@pytest.mark.parametrize(
    ("operation_global", "user_global", "allowed"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
@pytest.mark.asyncio
async def test_global_backfill_job_requires_both_global_reaches(
    monkeypatch, operation_global, user_global, allowed,
):
    """A real Global agent is visible only to a caller authorized for Global."""
    job = SimpleNamespace(agent_id=uuid4(), requested_by=uuid4())
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(organization_id=None)))
    caller = SimpleNamespace()
    org = uuid4()
    operation = OrgReach(everything=operation_global, organization_ids=frozenset({org}))
    original_user = OrgReach(everything=user_global, organization_ids=frozenset({org}))
    monkeypatch.setattr(agent_runs, "user_reach", lambda _caller: original_user)

    assert await agent_runs._backfill_job_in_reach(db, caller, job, operation) is allowed


@pytest.mark.asyncio
async def test_deleted_agent_backfill_job_remains_hidden(monkeypatch):
    job = SimpleNamespace(agent_id=uuid4(), requested_by=uuid4())
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    monkeypatch.setattr(agent_runs, "user_reach", lambda _caller: OrgReach(everything=True))

    assert not await agent_runs._backfill_job_in_reach(
        db, SimpleNamespace(), job, OrgReach(everything=True)
    )
