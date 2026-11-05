"""``GET /api/executions/{id}`` reads PostgreSQL only.

``shared.sdk_execution_reads.get_sdk_execution`` (serving
``GET /api/executions/{id}`` and ``workflows.get()``) answers from the
execution row; every run has one from the moment it is accepted.
"""

from uuid import uuid4

import pytest

from shared.sdk_execution_reads import SdkExecutionReadError, get_sdk_execution
from src.core.principal import UserPrincipal
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


@pytest.mark.asyncio
async def test_database_row_is_returned(db_session):
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

    result = await get_sdk_execution(db_session, _admin(admin_id), row.id)

    assert result.execution_id == str(row.id)
    assert result.status == ExecutionStatus.SUCCESS


@pytest.mark.asyncio
async def test_missing_row_is_not_found(db_session):
    with pytest.raises(SdkExecutionReadError) as exc_info:
        await get_sdk_execution(db_session, _admin(), uuid4())

    assert exc_info.value.status_code == 404
