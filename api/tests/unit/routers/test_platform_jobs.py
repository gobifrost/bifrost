from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.auth import ExecutionContext
from src.core.principal import UserPrincipal
from src.models.contracts.platform_jobs import PlatformJobStatus
from src.models.orm.platform_jobs import PlatformJob
from src.routers.platform_jobs import list_platform_jobs
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


def _caller(user: UserPrincipal) -> Caller:
    return Caller(
        user,
        AuthorizationContext(
            user_id=user.user_id,
            home_organization_id=user.organization_id,
            base_role_id=uuid4(),
            base_permissions=frozenset(),
            is_external=False,
        ),
    )


def _job(
    *, title: str, status: str, created_at: datetime, requested_by_user_id: str
) -> PlatformJob:
    return PlatformJob(
        job_type="solution.deploy",
        payload={},
        requested_by_user_id=requested_by_user_id,
        requested_by_email="operator@example.com",
        requested_by_name="Operator",
        title=title,
        status=status,
        phase="Test phase",
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_platform_jobs_list_is_paginated_and_orders_active_jobs_first(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    user_id = uuid4()
    active = _job(
        title="Older active deploy",
        status="running",
        created_at=now - timedelta(minutes=2),
        requested_by_user_id=str(user_id),
    )
    newest = _job(
        title="Newest completed deploy",
        status="succeeded",
        created_at=now,
        requested_by_user_id=str(user_id),
    )
    failed = _job(
        title="Failed deploy",
        status="failed",
        created_at=now - timedelta(minutes=1),
        requested_by_user_id=str(user_id),
    )
    db_session.add_all([active, newest, failed])
    await db_session.flush()

    user = UserPrincipal(
        user_id=user_id,
        email="admin@example.com",
        organization_id=uuid4(),
    )
    ctx = ExecutionContext(user=user, org_id=user.organization_id, db=db_session)
    monkeypatch.setattr("src.routers.platform_jobs.load_caller", AsyncMock(return_value=_caller(user)))

    first_page = await list_platform_jobs(
        ctx,
        user,
        active_only=False,
        limit=2,
        offset=0,
        job_status=None,
        search=None,
    )
    assert first_page.total == 3
    assert first_page.limit == 2
    assert first_page.offset == 0
    assert [job.id for job in first_page.jobs] == [active.id, newest.id]

    second_page = await list_platform_jobs(
        ctx,
        user,
        active_only=False,
        limit=2,
        offset=2,
        job_status=None,
        search=None,
    )
    assert [job.id for job in second_page.jobs] == [failed.id]

    active_only = await list_platform_jobs(
        ctx,
        user,
        active_only=True,
        limit=25,
        offset=0,
        job_status=None,
        search=None,
    )
    assert active_only.total == 1
    assert [job.id for job in active_only.jobs] == [active.id]


@pytest.mark.asyncio
async def test_platform_jobs_list_filters_by_status_and_search(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    user_id = uuid4()
    matching = _job(
        title="Production deploy",
        status="failed",
        created_at=now,
        requested_by_user_id=str(user_id),
    )
    db_session.add_all(
        [
            matching,
            _job(
                title="Development deploy",
                status="succeeded",
                created_at=now,
                requested_by_user_id=str(user_id),
            ),
            _job(
                title="Production cleanup",
                status="succeeded",
                created_at=now,
                requested_by_user_id=str(user_id),
            ),
        ]
    )
    await db_session.flush()

    user = UserPrincipal(
        user_id=user_id,
        email="admin@example.com",
        organization_id=uuid4(),
    )
    ctx = ExecutionContext(user=user, org_id=user.organization_id, db=db_session)
    monkeypatch.setattr("src.routers.platform_jobs.load_caller", AsyncMock(return_value=_caller(user)))

    response = await list_platform_jobs(
        ctx,
        user,
        active_only=False,
        limit=25,
        offset=0,
        job_status=PlatformJobStatus.FAILED,
        search="production deploy",
    )

    assert response.total == 1
    assert [job.id for job in response.jobs] == [matching.id]


@pytest.mark.asyncio
async def test_platform_jobs_list_filters_unowned_rows_before_limit(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    user_id = uuid4()
    own = _job(
        title="Owned", status="queued", created_at=now - timedelta(minutes=1),
        requested_by_user_id=str(user_id),
    )
    other = _job(
        title="Other", status="queued", created_at=now,
        requested_by_user_id=str(uuid4()),
    )
    db_session.add_all([own, other])
    await db_session.flush()
    user = UserPrincipal(user_id=user_id, email="person@example.com", organization_id=uuid4())
    ctx = ExecutionContext(user=user, org_id=user.organization_id, db=db_session)
    monkeypatch.setattr("src.routers.platform_jobs.load_caller", AsyncMock(return_value=_caller(user)))

    response = await list_platform_jobs(
        ctx, user, active_only=False, limit=1, offset=0, job_status=None, search=None
    )

    assert response.total == 1
    assert [job.id for job in response.jobs] == [own.id]
