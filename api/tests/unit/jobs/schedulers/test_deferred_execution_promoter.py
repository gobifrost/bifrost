"""Unit tests for the deferred execution promoter."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


PATH_PUBLISH = "src.jobs.schedulers.deferred_execution_promoter._publish_pending"
PATH_DB_CTX = "src.jobs.schedulers.deferred_execution_promoter.get_db_context"


def _new_scheduled(when: datetime) -> Execution:
    # workflow_id/executed_by left None to avoid FK constraints in unit tests.
    return Execution(
        id=uuid4(),
        workflow_id=None,
        workflow_name="demo",
        status=ExecutionStatus.SCHEDULED,
        parameters={"k": 1},
        scheduled_at=when,
        executed_by=None,
        executed_by_name="user",
    )


class _DbCtx:
    """Async context manager that yields the test's session.

    Ensures the promoter runs against the same engine/event loop as the
    test fixture, avoiding cross-loop task errors from the global engine.
    """

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_promotes_due_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    due = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()) as pub,
    ):
        promoted, failed = await promote_due_executions()

    assert promoted == 1
    assert failed == 0
    pub.assert_awaited_once()
    assert pub.await_args.kwargs["execution_record_exists"] is True

    await db_session.refresh(due)
    assert due.status == ExecutionStatus.PENDING


@pytest.mark.asyncio
async def test_leaves_future_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    future = _new_scheduled(datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(future)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()) as pub,
    ):
        promoted, failed = await promote_due_executions()

    assert promoted == 0
    pub.assert_not_awaited()

    await db_session.refresh(future)
    assert future.status == ExecutionStatus.SCHEDULED


@pytest.mark.asyncio
async def test_skips_cancelled_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    cancelled = _new_scheduled(datetime.now(timezone.utc) - timedelta(minutes=1))
    cancelled.status = ExecutionStatus.CANCELLED
    db_session.add(cancelled)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()),
    ):
        promoted, _ = await promote_due_executions()

    assert promoted == 0
    await db_session.refresh(cancelled)
    assert cancelled.status == ExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_reverts_on_publish_failure(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    due = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(side_effect=RuntimeError("rabbit down"))),
    ):
        promoted, failed = await promote_due_executions()

    assert promoted == 0
    assert failed == 1

    await db_session.refresh(due)
    # Reverted so next tick can retry.
    assert due.status == ExecutionStatus.SCHEDULED
