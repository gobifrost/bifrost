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
