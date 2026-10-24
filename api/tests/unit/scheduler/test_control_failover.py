"""Trigger leadership between control replicas against real PostgreSQL leases.

Each ``ControlPlane`` here stands in for one control replica (an API pod or a
scheduler). They share a database lease, exactly as replicas of different
hosts and versions do in a rollout. A probe task records every trigger it
runs so the tests can prove that triggers never overlap across owners.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.orm.scheduler_leases import SchedulerLease
from src.scheduler import control as control_module
from src.scheduler import leadership
from src.scheduler.control import ControlPlane
from src.scheduler.leadership import LOCAL_DEADLINE_MARGIN_SECONDS, SchedulerLeadershipLease
from src.scheduler.registry import ScheduledTaskDefinition

LEASE = timedelta(seconds=4)
RENEW_SECONDS = 0.5
RETRY_SECONDS = 0.2
PROBE = ScheduledTaskDefinition(
    "failover_probe",
    "Failover Probe",
    "Every 100 ms",
    callback="probe",
    trigger_args={"seconds": 0.1},  # type: ignore[dict-item]
    run_at_startup=True,
)


@dataclass
class Run:
    owner: str
    started: float
    ended: float


@pytest_asyncio.fixture
async def lease_name(
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
):
    name = f"scheduler-triggers-failover-{uuid4()}"

    @asynccontextmanager
    async def context() -> AsyncGenerator[AsyncSession, None]:
        async with async_session_factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(leadership, "get_db_context", context)
    monkeypatch.setattr(control_module, "start_scheduler_run", AsyncMock())
    monkeypatch.setattr(control_module, "finish_scheduler_run", AsyncMock())
    monkeypatch.setattr(control_module, "publish_task_states", AsyncMock())
    yield name
    async with context() as db:
        await db.execute(delete(SchedulerLease).where(SchedulerLease.name == name))


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[Run]:
    recorded: list[Run] = []
    current_owner: dict[int, str] = {}

    def resolve(_reference: str):  # type: ignore[no-untyped-def]
        task = asyncio.current_task()
        owner = current_owner[id(task)] if task and id(task) in current_owner else ""

        async def probe() -> None:
            started = time.monotonic()
            await asyncio.sleep(0.03)
            recorded.append(Run(owner, started, time.monotonic()))

        return probe

    real_start = ControlPlane._start_leader_services

    async def start_with_owner(self: ControlPlane) -> None:
        task = asyncio.current_task()
        if task is not None:
            current_owner[id(task)] = self.owner_id
        await real_start(self)

    monkeypatch.setattr(control_module, "resolve_callback", resolve)
    monkeypatch.setattr(ControlPlane, "_start_leader_services", start_with_owner)
    return recorded


def _replica(owner: str, lease_name: str) -> ControlPlane:
    return ControlPlane(
        leadership_lease=SchedulerLeadershipLease(
            owner_id=owner,
            name=lease_name,
            lease_duration=LEASE,
        ),
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        catalog=(PROBE,),
        renew_seconds=RENEW_SECONDS,
        retry_seconds=RETRY_SECONDS,
    )


async def _owner(lease_name: str) -> str | None:
    async with leadership.get_db_context() as db:
        return await db.scalar(
            select(SchedulerLease.owner_id).where(SchedulerLease.name == lease_name)
        )


async def _until(predicate, timeout: float = 10.0) -> float:  # type: ignore[no-untyped-def]
    started = time.monotonic()
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.05)
    return time.monotonic() - started


def _leads(replica: ControlPlane) -> bool:
    return replica._scheduler is not None and replica.lease.holds_lease()


def _assert_no_overlap(runs: list[Run]) -> None:
    ordered = sorted(runs, key=lambda run: run.started)
    for previous, following in zip(ordered, ordered[1:]):
        if previous.owner != following.owner:
            assert previous.ended <= following.started, (previous, following)


async def _leader_of(*replicas: ControlPlane) -> ControlPlane:
    async def one_leads() -> bool:
        return sum(_leads(replica) for replica in replicas) == 1

    await _until(one_leads)
    return next(replica for replica in replicas if _leads(replica))


@pytest.mark.asyncio
async def test_replicas_elect_one_leader_and_fail_over_after_crash(
    lease_name: str,
    runs: list[Run],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = _replica("api-a", lease_name), _replica("api-b", lease_name)
    await asyncio.gather(a.start(), b.start())
    try:
        leader = await _leader_of(a, b)
        follower = b if leader is a else a
        await asyncio.sleep(1)
        assert {run.owner for run in runs} == {leader.owner_id}
        assert await _owner(lease_name) == leader.owner_id

        # A crashed replica never releases its row; the follower must wait
        # for database expiry instead of taking over immediately.
        monkeypatch.setattr(leader.lease, "release", AsyncMock())
        crashed_at = time.monotonic()
        await leader.stop()

        async def follower_leads() -> bool:
            return _leads(follower)

        await _until(follower_leads)
        handoff = time.monotonic() - crashed_at
        assert handoff >= LEASE.total_seconds() - RENEW_SECONDS - 0.5
        assert handoff <= LEASE.total_seconds() + RETRY_SECONDS + 2
        await asyncio.sleep(0.5)
        assert runs[-1].owner == follower.owner_id
        _assert_no_overlap(runs)
    finally:
        await asyncio.gather(a.stop(), b.stop())


@pytest.mark.asyncio
async def test_rolling_restart_hands_off_promptly_without_overlap(
    lease_name: str,
    runs: list[Run],
) -> None:
    old = _replica("api-old", lease_name)
    await old.start()
    new = _replica("api-new", lease_name)
    try:
        await _leader_of(old)
        await new.start()
        await asyncio.sleep(0.5)
        assert not _leads(new)

        # Graceful shutdown releases the lease; the successor does not wait
        # for expiry.
        stopped_at = time.monotonic()
        await old.stop()

        async def new_leads() -> bool:
            return _leads(new)

        await _until(new_leads)
        assert time.monotonic() - stopped_at < LEASE.total_seconds() - 1
        await asyncio.sleep(0.5)
        assert {run.owner for run in runs} == {"api-old", "api-new"}
        _assert_no_overlap(runs)
    finally:
        await asyncio.gather(old.stop(), new.stop())


@pytest.mark.asyncio
async def test_partitioned_leader_stops_triggers_before_successor_starts(
    lease_name: str,
    runs: list[Run],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live leader that cannot reach the database must fence itself."""
    a, b = _replica("api-a", lease_name), _replica("api-b", lease_name)
    await a.start()
    try:
        await _leader_of(a)
        await b.start()

        async def unreachable() -> bool:
            await asyncio.Future()
            return True

        async def release_unreachable() -> None:
            # Release clears local state before its (unreachable) DB write.
            a.lease.lease_token = None

        async def acquire_unreachable() -> bool:
            raise ConnectionError("database unreachable")

        monkeypatch.setattr(a.lease, "renew", unreachable)
        monkeypatch.setattr(a.lease, "release", release_unreachable)
        monkeypatch.setattr(a.lease, "try_acquire", acquire_unreachable)
        partitioned_at = time.monotonic()

        async def b_leads() -> bool:
            return _leads(b)

        await _until(b_leads)
        await asyncio.sleep(0.5)

        a_runs = [run for run in runs if run.owner == "api-a"]
        b_runs = [run for run in runs if run.owner == "api-b"]
        assert a_runs and b_runs
        # The stale leader's last trigger started inside its local deadline.
        assert max(run.started for run in a_runs) <= (
            partitioned_at + LEASE.total_seconds() - LOCAL_DEADLINE_MARGIN_SECONDS
        )
        assert not _leads(a)
        _assert_no_overlap(runs)
    finally:
        await asyncio.gather(a.stop(), b.stop())
