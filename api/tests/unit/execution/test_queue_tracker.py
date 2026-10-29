"""Canonical PostgreSQL queue snapshots for workflow executions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.work_dispatches import WorkDispatch
from src.services.execution.queue_tracker import get_queued_executions, publish_queue_positions
from src.services.execution.workflow_dispatch import FAMILY


def _execution(execution_id: UUID, *, status: ExecutionStatus = ExecutionStatus.PENDING) -> Execution:
    return Execution(
        id=execution_id,
        workflow_name=f"queue-tracker-{execution_id}",
        status=status,
        executed_by_name="Queue Tracker",
    )


def _dispatch(
    execution_id: UUID,
    *,
    created_at: datetime,
    sync: bool | None = None,
    assigned_worker_id: UUID | None = None,
    family: str = FAMILY,
    dispatch_id: UUID | None = None,
) -> WorkDispatch:
    payload = {} if sync is None else {"sync": sync}
    return WorkDispatch(
        id=dispatch_id or uuid4(),
        family=family,
        subject_id=execution_id,
        handler="workflow.execute",
        handler_version=1,
        message={},
        payload=payload,
        assigned_worker_id=assigned_worker_id,
        memory_request_bytes=1,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_canonical_queue_includes_only_async_pending_dispatches_in_dispatch_order(
    db_session: AsyncSession,
) -> None:
    """Pending dispatch rows, not Redis, define membership and queue order."""
    base = datetime(2026, 10, 27, tzinfo=timezone.utc)
    first_id = UUID("00000000-0000-0000-0000-000000000010")
    second_id = UUID("00000000-0000-0000-0000-000000000011")
    assigned_id = UUID("00000000-0000-0000-0000-000000000012")
    sync_id = UUID("00000000-0000-0000-0000-000000000013")
    running_id = UUID("00000000-0000-0000-0000-000000000014")
    other_family_id = UUID("00000000-0000-0000-0000-000000000015")
    no_dispatch_id = UUID("00000000-0000-0000-0000-000000000016")

    db_session.add_all(
        [
            _execution(first_id),
            _execution(second_id),
            _execution(assigned_id),
            _execution(sync_id),
            _execution(running_id, status=ExecutionStatus.RUNNING),
            _execution(other_family_id),
            _execution(no_dispatch_id),
            _dispatch(
                second_id,
                created_at=base,
                dispatch_id=UUID("00000000-0000-0000-0000-000000000001"),
            ),
            _dispatch(
                first_id,
                created_at=base,
                dispatch_id=UUID("00000000-0000-0000-0000-000000000002"),
            ),
            _dispatch(
                assigned_id,
                created_at=base + timedelta(seconds=2),
                assigned_worker_id=uuid4(),
            ),
            _dispatch(sync_id, created_at=base + timedelta(seconds=3), sync=True),
            _dispatch(running_id, created_at=base + timedelta(seconds=4)),
            _dispatch(
                other_family_id,
                created_at=base + timedelta(seconds=5),
                family="agent_run",
            ),
        ]
    )
    await db_session.flush()

    queued = await get_queued_executions(db_session)

    assert [(entry.execution_id, entry.queued_at) for entry in queued] == [
        (second_id, base),
        (first_id, base),
        (assigned_id, base + timedelta(seconds=2)),
    ]


@pytest.mark.asyncio
async def test_queue_snapshot_publishes_positions_from_the_canonical_database_rows(
    async_session_factory,
) -> None:
    first_id, second_id = uuid4(), uuid4()
    base = datetime(2026, 10, 27, tzinfo=timezone.utc)
    async with async_session_factory() as session:
        session.add_all(
            [
                _execution(first_id),
                _execution(second_id),
                _dispatch(first_id, created_at=base),
                _dispatch(second_id, created_at=base + timedelta(seconds=1)),
            ]
        )
        await session.commit()

    try:
        with patch("src.core.pubsub.publish_execution_update", new=AsyncMock()) as publish:
            await publish_queue_positions()

        assert publish.await_args_list == [
            ((str(first_id), "Pending", {"queuePosition": 1, "waitReason": "queued"}), {}),
            ((str(second_id), "Pending", {"queuePosition": 2, "waitReason": "queued"}), {}),
        ]
    finally:
        async with async_session_factory() as session:
            await session.execute(
                WorkDispatch.__table__.delete().where(WorkDispatch.subject_id.in_([first_id, second_id]))
            )
            await session.execute(Execution.__table__.delete().where(Execution.id.in_([first_id, second_id])))
            await session.commit()
