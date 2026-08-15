from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform.application_publish import (
    APPLICATION_PUBLISH_DEFINITION,
    ApplicationPublishPayload,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services import platform_jobs as service


async def _enqueue(db_session: AsyncSession) -> PlatformJob:
    app_id = uuid4()
    job, reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Publishing Test",
        action_url="/apps/test/edit",
    )
    assert reused is False
    return job


@pytest.mark.asyncio
async def test_enqueue_reuses_only_active_dedupe_key(
    db_session: AsyncSession,
) -> None:
    app_id = uuid4()
    kwargs = {
        "dedupe_key": str(app_id),
        "organization_id": None,
        "requested_by_user_id": uuid4(),
        "requested_by_email": "dev@example.com",
        "requested_by_name": "Dev",
        "resource_type": "application",
        "resource_id": str(app_id),
        "title": "Publishing Test",
        "action_url": "/apps/test/edit",
    }
    first, first_reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id),
        **kwargs,
    )
    second, second_reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id),
        **kwargs,
    )
    assert first_reused is False
    assert second_reused is True
    assert second.id == first.id

    first.status = "failed"
    await db_session.commit()
    retry, retry_reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=app_id, message="Retry"),
        **kwargs,
    )
    assert retry_reused is False
    assert retry.id != first.id


@pytest.mark.asyncio
async def test_notification_is_one_projection_of_durable_job(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    notification_id = uuid4()
    notifications = MagicMock()
    notifications.create_notification = AsyncMock(
        return_value=SimpleNamespace(id=str(notification_id))
    )
    monkeypatch.setattr(
        service,
        "get_notification_service",
        lambda: notifications,
    )

    first = await service.ensure_platform_job_notification(db_session, job)
    second = await service.ensure_platform_job_notification(db_session, job)

    assert first == second == notification_id
    notifications.create_notification.assert_awaited_once()
    request = notifications.create_notification.await_args.kwargs["request"]
    assert request.metadata == {
        "job_id": str(job.id),
        "job_type": "application.publish",
        "resource_type": "application",
        "resource_id": job.resource_id,
        "action_url": "/apps/test/edit",
    }


@pytest.mark.asyncio
async def test_websocket_event_matches_public_http_contract_and_hides_payload(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    await db_session.commit()
    broadcast = AsyncMock()
    monkeypatch.setattr(service.pubsub_manager, "broadcast", broadcast)

    await service.publish_platform_job_update(job)

    channel, event = broadcast.await_args.args
    assert channel == f"notification:{job.requested_by_user_id}"
    assert event["type"] == "platform_job_updated"
    assert event["job"] == service.platform_job_to_public(job).model_dump(
        mode="json"
    )
    assert "payload" not in event["job"]
    assert "requested_by_email" not in event["job"]


@pytest.mark.asyncio
async def test_progress_and_terminal_writes_are_fenced(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    # The test stack also runs the real scheduler. Model a live lease so its
    # recovery loop cannot correctly reclaim this synthetic in-flight job.
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    published = AsyncMock()
    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", published)

    initial_revision = job.revision
    assert not await service.update_platform_job_progress(
        job.id,
        uuid4(),
        phase="stale",
        current=1,
        total=2,
        percent=50,
    )
    assert await service.update_platform_job_progress(
        job.id,
        token,
        phase="working",
        current=1,
        total=2,
        percent=50,
    )
    assert not await service.finish_platform_job(
        job.id,
        uuid4(),
        status="succeeded",
    )
    assert await service.finish_platform_job(
        job.id,
        token,
        status="succeeded",
        result={"ok": True},
    )

    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.phase == "Completed"
    assert job.progress_percent == 100
    assert job.result == {"ok": True}
    assert job.lease_token is None
    assert job.revision == initial_revision + 2
    assert published.await_count == 2


@pytest.mark.asyncio
async def test_cancel_is_idempotent(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    monkeypatch.setattr(
        service,
        "publish_platform_job_update",
        AsyncMock(),
    )
    job, first = await service.request_platform_job_cancel(db_session, job)
    job, second = await service.request_platform_job_cancel(db_session, job)
    assert first is True
    assert second is False
    assert job.status == "cancelled"
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_non_interruptible_handler_rejects_running_cancel(
    db_session: AsyncSession,
) -> None:
    job = await _enqueue(db_session)
    job.status = "running"
    job.lease_token = uuid4()
    job.lease_owner = "scheduler-a"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()
    job, accepted = await service.request_platform_job_cancel(db_session, job)
    assert accepted is False
    assert job.status == "running"
    assert job.cancel_requested_at is None


@pytest.mark.asyncio
async def test_deferred_job_releases_lease_and_finishes_from_child_work(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    job.lease_owner = "scheduler-a"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    published = AsyncMock()
    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", published)

    assert await service.defer_platform_job(
        job.id,
        token,
        phase="Waiting for child work",
        result={"children": 2},
    )
    await db_session.refresh(job)
    assert job.status == "waiting"
    assert job.lease_token is None
    assert job.lease_owner is None
    assert service.platform_job_to_public(job).can_cancel is True

    assert await service.update_deferred_platform_job_progress(
        job.id,
        phase="Completed 1/2",
        current=1,
        total=2,
    )
    assert await service.finish_deferred_platform_job(
        job.id,
        status="succeeded",
        result={"children": 2, "succeeded": 2},
    )
    await db_session.refresh(job)
    assert job.status == "succeeded"
    assert job.progress_percent == 100
    assert job.result == {"children": 2, "succeeded": 2}
    assert job.completed_at is not None
    assert published.await_count == 3


@pytest.mark.asyncio
async def test_stale_runner_cannot_defer_job(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.status = "running"
    job.lease_token = uuid4()
    job.lease_owner = "scheduler-a"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert not await service.defer_platform_job(
        job.id,
        uuid4(),
        phase="Stale child work",
    )
    await db_session.refresh(job)
    assert job.status == "running"
