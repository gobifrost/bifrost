"""Remote API-owner context is carried by the tasks that mutate work."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from src.models.orm.execution_workers import ExecutionWorker
from src.worker.remote.fencing import LeaseRef, fence_remote_owner, remote_owner_context
from src.worker.remote.ownership import RemoteOwnerLease


def _lease(generation: int = 1) -> RemoteOwnerLease:
    return RemoteOwnerLease(
        worker_id=uuid4(),
        owner_id=uuid4(),
        launcher_id=uuid4(),
        session_generation=generation,
    )


def test_remote_owner_context_observes_a_same_owner_reconnect() -> None:
    ref = LeaseRef(_lease())

    with remote_owner_context(ref):
        assert ref.lease.session_generation == 1
        ref.lease = replace(ref.lease, session_generation=2)
        assert ref.lease.session_generation == 2


@pytest.mark.asyncio
async def test_fence_holds_the_worker_row_lock_until_the_result_transaction_commits(
    async_session_factory,
) -> None:
    worker_id, owner_id, launcher_id = uuid4(), uuid4(), uuid4()
    lease = RemoteOwnerLease(worker_id, owner_id, launcher_id, 1)
    async with async_session_factory() as session:
        session.add(
            ExecutionWorker(
                id=worker_id,
                form="static",
                state="ready",
                resource_class="standard",
                slots=0,
                remote_owner_id=owner_id,
                remote_launcher_id=launcher_id,
                remote_session_generation=1,
                remote_owner_lease_expires_at=(
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ),
            )
        )
        await session.commit()

    try:
        async with async_session_factory() as result_session:
            with remote_owner_context(LeaseRef(lease)):
                assert await fence_remote_owner(result_session)
                async with async_session_factory() as replacement_session:
                    await replacement_session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(DBAPIError):
                        await replacement_session.execute(
                            update(ExecutionWorker)
                            .where(ExecutionWorker.id == worker_id)
                            .values(remote_owner_id=uuid4())
                        )
                    await replacement_session.rollback()
                await result_session.commit()
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
            await session.commit()
