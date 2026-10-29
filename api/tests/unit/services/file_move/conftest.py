"""Fixtures for files.move engine and job tests (real Postgres, fake S3)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete

from src.core.database import get_db_context
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.file_storage.move import MoveTarget

from .support import FakeStore


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture(autouse=True)
def published(monkeypatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr("src.core.pubsub.publish_file_change", mock)
    return mock


@pytest_asyncio.fixture
async def solution_target():
    org_id, solution_id = uuid4(), uuid4()
    async with get_db_context() as db:
        db.add(Organization(id=org_id, name=f"Move-{org_id.hex[:8]}", created_by="test"))
        await db.flush()
        db.add(Solution(id=solution_id, slug=f"move-{solution_id.hex[:8]}", name="Move", organization_id=org_id))
    yield MoveTarget(
        location="docs",
        scope=str(solution_id),
        solution_id=solution_id,
        organization_id=org_id,
        requested_by=uuid4(),
    )
    async with get_db_context() as db:
        await db.execute(delete(Solution).where(Solution.id == solution_id))
        await db.execute(delete(Organization).where(Organization.id == org_id))


@pytest_asyncio.fixture
async def org_target():
    org_id = uuid4()
    async with get_db_context() as db:
        db.add(Organization(id=org_id, name=f"Move-{org_id.hex[:8]}", created_by="test"))
    yield MoveTarget(
        location="docs",
        scope=str(org_id),
        solution_id=None,
        organization_id=org_id,
        requested_by=uuid4(),
    )
    async with get_db_context() as db:
        await db.execute(delete(Organization).where(Organization.id == org_id))
