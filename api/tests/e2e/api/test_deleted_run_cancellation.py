"""User deletion cancels their unstarted durable workflow runs."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.run_cancellation import RUN_USER_DELETED_ERROR
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_delete_user_cancels_scheduled_delayed_and_queued_runs(
    e2e_client,
    platform_admin,
    org1,
    db_session: AsyncSession,
):
    create = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": "departed-run-user@contoso.example",
            "name": "Departed Run User",
            "organization_id": org1["id"],
        },
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])

    scheduled = Execution(
        workflow_name="scheduled-run",
        executed_by=user_id,
        executed_by_name="Departed Run User",
        run_user_id=user_id,
        status=ExecutionStatus.SCHEDULED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    delayed = Execution(
        workflow_name="delayed-run",
        executed_by=user_id,
        executed_by_name="Departed Run User",
        run_user_id=user_id,
        status=ExecutionStatus.SCHEDULED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    queued = Execution(
        workflow_name="queued-run",
        executed_by=user_id,
        executed_by_name="Departed Run User",
        run_user_id=user_id,
        status=ExecutionStatus.PENDING,
    )
    db_session.add_all([scheduled, delayed, queued])
    await db_session.flush()
    execution_ids = [scheduled.id, delayed.id, queued.id]
    try:
        await db_session.commit()

        deleted = e2e_client.delete(
            f"/api/users/{user_id}", headers=platform_admin.headers
        )
        assert deleted.status_code == 204, deleted.text

        await db_session.rollback()
        rows = list(
            (
                await db_session.execute(
                    select(
                        Execution.status, Execution.error_message,
                        Execution.run_user_id, Execution.completed_at,
                    ).where(Execution.id.in_(execution_ids))
                )
            ).all()
        )
        assert len(rows) == 3
        for row in rows:
            assert (row.status, row.error_message, row.run_user_id) == (
                ExecutionStatus.CANCELLED,
                RUN_USER_DELETED_ERROR,
                None,
            )
            assert row.completed_at is not None
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(Execution).where(Execution.id.in_(execution_ids))
        )
        await db_session.commit()
        e2e_client.delete(f"/api/users/{user_id}", headers=platform_admin.headers)
