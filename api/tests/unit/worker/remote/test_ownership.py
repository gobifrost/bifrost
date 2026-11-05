"""Persisted remote API owner-generation fencing contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.execution_workers import ExecutionWorker
from src.worker.remote import ownership


@pytest.mark.asyncio
async def test_active_owner_blocks_replacement_and_stale_generation_cannot_renew(
    async_session_factory, monkeypatch
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(ownership, "get_liveness_db_context", context)
    worker_id = uuid4()
    async with async_session_factory() as session:
        session.add(ExecutionWorker(
            id=worker_id, form="job", state="ready", resource_class="standard", slots=1,
        ))
        await session.commit()
    owner, replacement, launcher = uuid4(), uuid4(), uuid4()
    try:
        first = await ownership.acquire_remote_owner(
            worker_id=worker_id, owner_id=owner, launcher_id=launcher,
            nonce_digest="a" * 64, hello_sequence=1,
        )
        assert first is not None
        assert await ownership.acquire_remote_owner(
            worker_id=worker_id, owner_id=replacement, launcher_id=launcher,
            nonce_digest="b" * 64, hello_sequence=2,
        ) is None

        current = await ownership.acquire_remote_owner(
            worker_id=worker_id, owner_id=owner, launcher_id=launcher,
            nonce_digest="b" * 64, hello_sequence=2,
        )
        assert current is not None
        assert current.session_generation == first.session_generation + 1
        assert not await ownership.renew_remote_owner(first)
        assert not await ownership.owns_remote_session(first)
        assert await ownership.owns_remote_session(current)
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await session.commit()


@pytest.mark.asyncio
async def test_terminal_ack_holds_owner_lock_until_its_bounded_send_finishes(
    async_session_factory, monkeypatch,
) -> None:
    import asyncio
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, Mock

    from sqlalchemy import select
    from sqlalchemy.exc import DBAPIError

    from src.worker.remote.fencing import LeaseRef
    from src.worker.remote.host import Bridge

    worker_id, owner_id, launcher_id = uuid4(), uuid4(), uuid4()
    lease = ownership.RemoteOwnerLease(worker_id, owner_id, launcher_id, 1)
    sending, finish_send = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def context():
        async with async_session_factory() as db:
            yield db
            await db.commit()

    async def send(_frame):
        sending.set()
        await finish_send.wait()

    monkeypatch.setattr("src.worker.remote.host.get_db_context", context)
    async with async_session_factory() as db:
        db.add(ExecutionWorker(
            id=worker_id, form="static", state="ready", resource_class="standard", slots=1,
            remote_owner_id=owner_id, remote_launcher_id=launcher_id, remote_session_generation=1,
            remote_owner_lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        ))
        await db.commit()
    bridge = Bridge(Mock(send_text=AsyncMock(side_effect=send)), {
        "current": 1, "limit": 100, "hostname": "test", "requirements": {},
    }, lease)
    bridge.lease_ref = LeaseRef(lease)
    ack = asyncio.create_task(bridge.acknowledge("terminal"))
    try:
        await sending.wait()
        # A cross-host owner update requires this exclusive lock. It cannot
        # pass the ACK's shared fence while the network send is suspended.
        async with async_session_factory() as db:
            with pytest.raises(DBAPIError) as blocked:
                await db.scalar(select(ExecutionWorker.id).where(
                    ExecutionWorker.id == worker_id
                ).with_for_update(nowait=True))
            assert blocked.value.orig.sqlstate == "55P03"
        finish_send.set()
        assert await ack is None
        async with async_session_factory() as db:
            assert await db.scalar(select(ExecutionWorker.id).where(
                ExecutionWorker.id == worker_id
            ).with_for_update(nowait=True)) == worker_id
    finally:
        finish_send.set()
        await asyncio.gather(ack, return_exceptions=True)
        async with async_session_factory() as db:
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await db.commit()
