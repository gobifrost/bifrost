from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.routers import agent_runs
from src.services.authorization.reach import OrgReach


@pytest.mark.asyncio
async def test_global_backfill_job_requires_global_operation_reach(monkeypatch):
    """A real Global agent is visible only to a caller authorized for Global."""
    job = SimpleNamespace(agent_id=uuid4(), requested_by=uuid4())
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(organization_id=None)))
    caller = SimpleNamespace()
    monkeypatch.setattr(agent_runs, "user_reach", lambda _caller: OrgReach(everything=True))

    assert await agent_runs._backfill_job_in_reach(
        db, caller, job, OrgReach(everything=True)
    )
    assert not await agent_runs._backfill_job_in_reach(
        db, caller, job, OrgReach(organization_ids=frozenset({uuid4()}))
    )


@pytest.mark.asyncio
async def test_deleted_agent_backfill_job_remains_hidden(monkeypatch):
    job = SimpleNamespace(agent_id=uuid4(), requested_by=uuid4())
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    monkeypatch.setattr(agent_runs, "user_reach", lambda _caller: OrgReach(everything=True))

    assert not await agent_runs._backfill_job_in_reach(
        db, SimpleNamespace(), job, OrgReach(everything=True)
    )
