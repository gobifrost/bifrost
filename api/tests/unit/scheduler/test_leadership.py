from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.scheduler_leases import SchedulerLease
from src.scheduler import leadership
from src.scheduler.leadership import SchedulerLeadershipLease


@pytest_asyncio.fixture
async def lease_name(
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
):
    test_lease_name = f"scheduler-triggers-test-{uuid4()}"

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
        await db.execute(
            delete(SchedulerLease).where(SchedulerLease.name == test_lease_name)
        )
    yield test_lease_name
    async with test_context() as db:
        await db.execute(
            delete(SchedulerLease).where(SchedulerLease.name == test_lease_name)
        )


@pytest.mark.asyncio
async def test_only_one_replica_acquires_trigger_leadership(lease_name: str) -> None:
    first = SchedulerLeadershipLease(owner_id="scheduler-a", name=lease_name)
    second = SchedulerLeadershipLease(owner_id="scheduler-b", name=lease_name)

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
async def test_expired_generation_cannot_renew_or_release_new_leader(
    lease_name: str,
) -> None:
    stale = SchedulerLeadershipLease(owner_id="scheduler-a", name=lease_name)
    replacement = SchedulerLeadershipLease(owner_id="scheduler-b", name=lease_name)
    assert await stale.try_acquire()

    async with leadership.get_db_context() as db:
        await db.execute(
            update(SchedulerLease)
            .where(SchedulerLease.name == lease_name)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )

    assert await replacement.try_acquire()
    assert not await stale.renew()
    await stale.release()
    assert await replacement.renew()
    await replacement.release()


@pytest.mark.asyncio
async def test_local_deadline_ends_before_the_database_lease(lease_name: str) -> None:
    clock = [100.0]
    lease = SchedulerLeadershipLease(
        owner_id="scheduler-a",
        name=lease_name,
        lease_duration=timedelta(seconds=30),
        monotonic=lambda: clock[0],
    )
    assert not lease.holds_lease()
    assert await lease.try_acquire()

    margin = leadership.LOCAL_DEADLINE_MARGIN_SECONDS
    assert lease.seconds_remaining() == pytest.approx(30 - margin)
    clock[0] += 30 - margin - 0.01
    assert lease.holds_lease()
    clock[0] += 0.02
    # The database still lists this owner, but the replica must already have
    # stopped producing triggers.
    assert not lease.holds_lease()
    assert lease.is_leader

    assert await lease.renew()
    assert lease.holds_lease()
    await lease.release()
    assert not lease.holds_lease()
