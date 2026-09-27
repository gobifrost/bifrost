"""Regression tests for the PostgreSQL-first pending execution read path.

``shared.sdk_execution_reads.get_sdk_execution`` (serving
``GET /api/executions/{id}`` and ``workflows.get()``) consults Redis for a
pending execution only after PostgreSQL misses — a worker that has not
persisted the row yet must not shadow the database, and a persisted row
must never be replaced by a stale pending copy.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from shared.sdk_execution_reads import SdkExecutionReadError, get_sdk_execution
from src.core.principal import UserPrincipal
from src.models import WorkflowExecution
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


def _admin(user_id=None) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id or uuid4(),
        email="pending-reads-admin@test.local",
        organization_id=None,
        name="Pending Reads Admin",
        is_superuser=True,
        is_verified=True,
    )


def _pending_execution(execution_id, user_id) -> WorkflowExecution:
    return WorkflowExecution(
        execution_id=str(execution_id),
        workflow_name="Pending execution",
        workflow_id=None,
        org_id=None,
        org_name="Global",
        form_id=None,
        executed_by=str(user_id),
        executed_by_name="Pending User",
        executed_by_email=None,
        status=ExecutionStatus.PENDING,
        input_data={},
        result=None,
        logs=[],
        started_at=None,
        completed_at=None,
    )


@pytest.mark.asyncio
async def test_database_hit_never_consults_redis(db_session):
    from src.models import User as UserORM

    admin_id = uuid4()
    db_session.add(
        UserORM(
            id=admin_id,
            email=f"pending-path-{admin_id.hex[:8]}@example.com",
            name="Admin",
            is_superuser=True,
        )
    )
    await db_session.flush()
    row = Execution(
        workflow_name="pending-path-db-hit",
        status=ExecutionStatus.SUCCESS,
        parameters={},
        executed_by=admin_id,
        executed_by_name="Admin",
    )
    db_session.add(row)
    await db_session.flush()

    with patch(
        "shared.pending_execution.get_pending_execution_fallback",
        new=AsyncMock(),
    ) as fallback:
        result = await get_sdk_execution(db_session, _admin(admin_id), row.id)

    assert result.execution_id == str(row.id)
    assert result.status == ExecutionStatus.SUCCESS
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_fallback_serves_pending_only_after_database_miss(db_session):
    execution_id = uuid4()
    user_id = uuid4()
    pending = _pending_execution(execution_id, user_id)

    with patch(
        "shared.pending_execution.get_pending_execution_fallback",
        new=AsyncMock(return_value=(pending, None)),
    ) as fallback:
        result = await get_sdk_execution(db_session, _admin(user_id), execution_id)

    assert result is pending
    fallback.assert_awaited_once()


@pytest.mark.asyncio
async def test_not_found_when_database_and_redis_miss(db_session):
    with (
        patch(
            "shared.pending_execution.get_pending_execution_fallback",
            new=AsyncMock(return_value=(None, "NotFound")),
        ),
        pytest.raises(SdkExecutionReadError) as exc_info,
    ):
        await get_sdk_execution(db_session, _admin(), uuid4())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_forbidden_pending_maps_to_403(db_session):
    with (
        patch(
            "shared.pending_execution.get_pending_execution_fallback",
            new=AsyncMock(return_value=(None, "Forbidden")),
        ),
        pytest.raises(SdkExecutionReadError) as exc_info,
    ):
        await get_sdk_execution(db_session, _admin(), uuid4())

    assert exc_info.value.status_code == 403
