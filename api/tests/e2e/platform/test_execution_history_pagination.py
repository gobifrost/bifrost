"""Execution History ordering and keyset pagination regressions."""

import base64
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.core.constants import PROVIDER_ORG_ID
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.users import User
from shared.sdk_execution_reads import decode_history_cursor, list_sdk_executions

pytestmark = pytest.mark.e2e


def _execution(
    *,
    name: str,
    status: ExecutionStatus,
    created_at: datetime,
    executed_by: UUID,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    scheduled_at: datetime | None = None,
) -> Execution:
    return Execution(
        id=uuid4(),
        workflow_name=name,
        status=status,
        parameters={},
        executed_by=executed_by,
        executed_by_name="History pagination test",
        created_at=created_at,
        started_at=started_at,
        completed_at=completed_at,
        scheduled_at=scheduled_at,
    )


@pytest.mark.asyncio
async def test_history_pages_use_the_display_timeline_without_gaps(db_session):
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    user_id = uuid4()
    workflow_name = f"history-pagination-{uuid4().hex}"
    db_session.add(
        User(
            id=user_id,
            email=f"history-pagination-{user_id}@example.com",
            name="History pagination test",
            organization_id=PROVIDER_ORG_ID,
        )
    )
    recent = [
        _execution(
            name=workflow_name,
            status=ExecutionStatus.SUCCESS,
            created_at=now - timedelta(minutes=index, seconds=5),
            executed_by=user_id,
            started_at=now - timedelta(minutes=index),
            completed_at=now - timedelta(minutes=index) + timedelta(seconds=2),
        )
        for index in range(30)
    ]
    pending = _execution(
        name=workflow_name,
        status=ExecutionStatus.PENDING,
        created_at=now - timedelta(seconds=30),
        executed_by=user_id,
    )
    future_scheduled = _execution(
        name=workflow_name,
        status=ExecutionStatus.SCHEDULED,
        created_at=now,
        executed_by=user_id,
        scheduled_at=now + timedelta(days=1),
    )
    stale_cancelled = _execution(
        name=workflow_name,
        status=ExecutionStatus.CANCELLED,
        created_at=now - timedelta(days=77, minutes=5),
        executed_by=user_id,
        scheduled_at=now - timedelta(days=77),
    )
    rows = [*recent, pending, future_scheduled, stale_cancelled]
    db_session.add_all(rows)
    await db_session.flush()

    # Superuser: scope resolves to ALL (no org filter) and the owner
    # filter is off. Filter by this test's unique workflow name so prior
    # E2E executions cannot enter the 33-row pagination universe. An org-user
    # principal would now resolve scope to its own org (the HTTP rule),
    # which would exclude these global rows; owner-only visibility is
    # pinned by the shared-service unit tests instead.
    principal = UserPrincipal(
        user_id=user_id,
        email="history-admin@example.com",
        organization_id=PROVIDER_ORG_ID,
        is_superuser=True,
    )
    first_page, token = await list_sdk_executions(
        db_session,
        principal,
        workflow_name=workflow_name,
        limit=25,
    )
    assert token is not None
    assert len(first_page) == 25
    assert first_page[0].execution_id == str(future_scheduled.id)
    assert first_page[-1].execution_id != str(stale_cancelled.id)

    second_page, final_token = await list_sdk_executions(
        db_session,
        principal,
        workflow_name=workflow_name,
        limit=25,
        cursor=decode_history_cursor(token),
    )

    all_ids = [row.execution_id for row in [*first_page, *second_page]]
    assert len(all_ids) == len(rows)
    assert len(set(all_ids)) == len(rows)
    assert all_ids[-1] == str(stale_cancelled.id)
    assert final_token is None


@pytest.mark.asyncio
@pytest.mark.parametrize("via_handler", [False, True], ids=["sdk", "http-handler"])
@pytest.mark.parametrize("legacy_started_at", [None, "2026-08-27T17:00:00+00:00"])
async def test_legacy_history_position_restarts_on_display_timeline(
    db_session, via_handler, legacy_started_at,
):
    now = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)
    user_id = uuid4()
    name = f"legacy-history-{uuid4().hex}"
    db_session.add(User(
        id=user_id, email=f"{name}@example.com", name="History pagination test",
        organization_id=PROVIDER_ORG_ID,
    ))
    rows = [
        _execution(
            name=name, status=ExecutionStatus.PENDING,
            created_at=now - timedelta(minutes=index), executed_by=user_id,
        )
        for index in range(3)
    ]
    db_session.add_all(rows)
    await db_session.flush()
    principal = UserPrincipal(
        user_id=user_id, email=f"{name}@example.com",
        organization_id=PROVIDER_ORG_ID, is_superuser=True,
    )
    legacy_token = base64.urlsafe_b64encode(json.dumps({
        "v": 1, "s": legacy_started_at, "i": str(rows[-1].id),
    }).encode()).decode()

    if via_handler:
        from src.routers.executions import list_executions

        result = await list_executions(
            SimpleNamespace(db=db_session, user=principal),
            SimpleNamespace(query_params={"continuationToken": legacy_token}),
            scope=None, workflowName=name, workflowId=None, status_filter=None,
            startDate=None, endDate=None, limit=2, continuationToken=None,
        )
        page, token = result.executions, result.continuation_token
    else:
        page, token = await list_sdk_executions(
            db_session, principal, workflow_name=name, limit=2,
            cursor=decode_history_cursor(legacy_token),
        )

    assert [row.execution_id for row in page] == [str(row.id) for row in rows[:2]]
    assert token is not None
    assert json.loads(base64.urlsafe_b64decode(token))["v"] == 2
