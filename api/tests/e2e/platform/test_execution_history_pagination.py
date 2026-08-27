"""Execution History ordering and keyset pagination regressions."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.routers.executions import ExecutionRepository, _decode_history_cursor

pytestmark = pytest.mark.e2e


def _execution(
    *,
    name: str,
    status: ExecutionStatus,
    created_at: datetime,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    scheduled_at: datetime | None = None,
) -> Execution:
    return Execution(
        id=uuid4(),
        workflow_name=name,
        status=status,
        parameters={},
        executed_by=None,
        executed_by_name="History pagination test",
        created_at=created_at,
        started_at=started_at,
        completed_at=completed_at,
        scheduled_at=scheduled_at,
    )


@pytest.mark.asyncio
async def test_history_pages_use_the_display_timeline_without_gaps(db_session):
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    recent = [
        _execution(
            name=f"recent-{index:02d}",
            status=ExecutionStatus.SUCCESS,
            created_at=now - timedelta(minutes=index, seconds=5),
            started_at=now - timedelta(minutes=index),
            completed_at=now - timedelta(minutes=index) + timedelta(seconds=2),
        )
        for index in range(30)
    ]
    pending = _execution(
        name="pending-with-created-at",
        status=ExecutionStatus.PENDING,
        created_at=now - timedelta(seconds=30),
    )
    future_scheduled = _execution(
        name="future-scheduled",
        status=ExecutionStatus.SCHEDULED,
        created_at=now,
        scheduled_at=now + timedelta(days=1),
    )
    stale_cancelled = _execution(
        name="stale-cancelled-scheduled",
        status=ExecutionStatus.CANCELLED,
        created_at=now - timedelta(days=77, minutes=5),
        scheduled_at=now - timedelta(days=77),
    )
    rows = [*recent, pending, future_scheduled, stale_cancelled]
    db_session.add_all(rows)
    await db_session.flush()

    principal = UserPrincipal(
        user_id=uuid4(),
        email="history-admin@example.com",
        organization_id=None,
        is_superuser=True,
    )
    repository = ExecutionRepository(db_session)

    first_page, token = await repository.list_executions(
        user=principal,
        org_id=None,
        limit=25,
    )
    assert token is not None
    assert len(first_page) == 25
    assert first_page[0].execution_id == str(future_scheduled.id)
    assert first_page[-1].execution_id != str(stale_cancelled.id)

    second_page, final_token = await repository.list_executions(
        user=principal,
        org_id=None,
        limit=25,
        cursor=_decode_history_cursor(token),
    )

    all_ids = [row.execution_id for row in [*first_page, *second_page]]
    assert len(all_ids) == len(rows)
    assert len(set(all_ids)) == len(rows)
    assert all_ids[-1] == str(stale_cancelled.id)
    assert final_token is None
