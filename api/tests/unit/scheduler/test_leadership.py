from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.scheduler_leases import SchedulerLease
from src.scheduler import leadership
from src.scheduler.leadership import (
    TRIGGER_LEASE_NAME,
    SchedulerLeadershipLease,
)


@pytest_asyncio.fixture(autouse=True)
async def scheduler_lease_context(
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
):
    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(leadership, "get_db_context", test_context)
    async with test_context() as db:
        await db.execute(delete(SchedulerLease))
    yield
    async with test_context() as db:
        await db.execute(delete(SchedulerLease))


@pytest.mark.asyncio
async def test_only_one_replica_acquires_trigger_leadership() -> None:
    first = SchedulerLeadershipLease(owner_id="scheduler-a")
    second = SchedulerLeadershipLease(owner_id="scheduler-b")

    results = await asyncio.gather(first.try_acquire(), second.try_acquire())

    assert results.count(True) == 1
    assert first.is_leader != second.is_leader

    winner = first if first.is_leader else second
    loser = second if first.is_leader else first
    assert await winner.renew()
    assert not await loser.renew()

    await winner.release()
    assert await loser.try_acquire()
    await loser.release()


@pytest.mark.asyncio
async def test_expired_generation_cannot_renew_or_release_new_leader() -> None:
    stale = SchedulerLeadershipLease(owner_id="scheduler-a")
    replacement = SchedulerLeadershipLease(owner_id="scheduler-b")
    assert await stale.try_acquire()

    async with leadership.get_db_context() as db:
        await db.execute(
            update(SchedulerLease)
            .where(SchedulerLease.name == TRIGGER_LEASE_NAME)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )

    assert await replacement.try_acquire()
    assert not await stale.renew()
    await stale.release()
    assert await replacement.renew()
    await replacement.release()
