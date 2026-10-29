from __future__ import annotations

import asyncio

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.app_build_errors import APP_BUILD_JOB_TYPES, app_build_memory_message
from src.jobs.platform.application_deploy import (
    APPLICATION_DEPLOY_DEFINITION,
    ApplicationDeployPayload,
)
from src.jobs.platform.application_publish import (
    APPLICATION_PUBLISH_DEFINITION,
    ApplicationPublishPayload,
    run_application_publish,
)
from src.jobs.platform.solution_export import (
    SOLUTION_EXPORT_DEFINITION,
    SolutionExportPayload,
)
from src.jobs.platform.solution_deploy import (
    SOLUTION_DEPLOY_DEFINITION,
    SolutionDeployPayload,
)
from src.jobs.platform.solution_git_sync import (
    SOLUTION_GIT_SYNC_DEFINITION,
    SolutionGitSyncPayload,
)
from src.jobs.platform.base import (
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
    PlatformJobRequiresAction,
)
from src.jobs.platform import runner
from src.models.contracts.platform_jobs import PlatformJobStatus
from src.models.orm.platform_jobs import PlatformJob
from src.services.audit_context import ActorContext, clear_actor, set_actor
from src.services import platform_jobs as service


async def _isolate_jobs(db_session: AsyncSession) -> None:
    """Turn isolated jobs on in Settings -> Kubernetes (rolled back with the session)."""
    from src.services.kubernetes_execution import KubernetesExecutionService

    await KubernetesExecutionService(db_session).update_policy(
        {"isolated_jobs_enabled": True}, updated_by="test"
    )


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
async def test_enqueue_persists_current_impersonation_provenance(
    db_session: AsyncSession,
) -> None:
    operator_id, session_id = uuid4(), uuid4()
    token = set_actor(
        ActorContext(
            user_id=uuid4(),
            organization_id=None,
            impersonated_by_user_id=operator_id,
            impersonation_session_id=session_id,
        )
    )
    try:
        job = await _enqueue(db_session)
    finally:
        clear_actor(token)

    assert job.impersonated_by_user_id == operator_id
    assert job.impersonation_session_id == session_id


@pytest.mark.asyncio
async def test_enqueue_wakes_executors_only_once_the_job_is_committed(
    async_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import delete

    from src.services import work_dispatch

    wake = AsyncMock()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    async with async_session_factory() as session:
        job = await _enqueue(session)
        await asyncio.sleep(0)
        wake.assert_not_awaited()  # executors would find nothing to claim yet

        await session.commit()
        await asyncio.sleep(0)
        wake.assert_awaited_once_with()

        await session.execute(delete(PlatformJob).where(PlatformJob.id == job.id))
        await session.commit()


@pytest.mark.asyncio
async def test_a_rolled_back_enqueue_wakes_nobody(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services import work_dispatch

    wake = AsyncMock()
    monkeypatch.setattr(work_dispatch, "wake_pool_workers", wake)
    await _enqueue(db_session)
    await db_session.rollback()
    await asyncio.sleep(0)

    wake.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_routes_build_class_jobs_to_configured_backend(
    db_session: AsyncSession,
) -> None:
    await _isolate_jobs(db_session)
    app_id = uuid4()
    job, reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_DEPLOY_DEFINITION,
        ApplicationDeployPayload(
            application_id=app_id,
            deployment_id=uuid4(),
            input_sha256="a" * 64,
        ),
        dedupe_key=str(app_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(app_id),
        title="Deploying App",
        action_url=None,
    )

    assert reused is False
    assert job.execution_backend == "kubernetes"


@pytest.mark.asyncio
async def test_enqueue_routes_full_solution_exports_to_isolated_build_backend(
    db_session: AsyncSession,
) -> None:
    await _isolate_jobs(db_session)
    export_id = uuid4()

    job, reused = await service.enqueue_platform_job(
        db_session,
        SOLUTION_EXPORT_DEFINITION,
        SolutionExportPayload(export_job_id=export_id),
        dedupe_key=str(export_id),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="solution_export",
        resource_id=str(export_id),
        title="Exporting solution",
        action_url=None,
    )

    assert reused is False
    assert job.execution_backend == "kubernetes"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("definition", "payload", "resource_type", "title"),
    [
        (
            SOLUTION_DEPLOY_DEFINITION,
            lambda: SolutionDeployPayload(
                deploy_job_id=uuid4(),
                kind="deploy",
                install_id=uuid4(),
                input_sha256="a" * 64,
                options={},
            ),
            "solution_deploy",
            "Deploying solution",
        ),
        (
            SOLUTION_GIT_SYNC_DEFINITION,
            lambda: SolutionGitSyncPayload(solution_id=uuid4()),
            "solution",
            "Syncing solution from Git",
        ),
    ],
    ids=["solution-deploy", "solution-git-sync"],
)
async def test_enqueue_routes_solution_build_jobs_to_configured_backend(
    db_session: AsyncSession,
    definition: PlatformJobDefinition,
    payload,
    resource_type: str,
    title: str,
) -> None:
    await _isolate_jobs(db_session)
    body = payload()
    resource_id = str(body.deploy_job_id if hasattr(body, "deploy_job_id") else body.solution_id)

    job, reused = await service.enqueue_platform_job(
        db_session,
        definition,
        body,
        dedupe_key=resource_id,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type=resource_type,
        resource_id=resource_id,
        title=title,
        action_url=None,
    )

    assert reused is False
    assert job.execution_backend == "kubernetes"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("definition", "payload"),
    [
        (
            SOLUTION_DEPLOY_DEFINITION,
            lambda: SolutionDeployPayload(
                deploy_job_id=uuid4(),
                kind="deploy",
                install_id=uuid4(),
                input_sha256="a" * 64,
                options={},
            ),
        ),
        (
            SOLUTION_GIT_SYNC_DEFINITION,
            lambda: SolutionGitSyncPayload(solution_id=uuid4()),
        ),
    ],
    ids=["solution-deploy", "solution-git-sync"],
)
async def test_enqueue_keeps_solution_build_jobs_local_when_isolation_is_off(
    db_session: AsyncSession,
    definition: PlatformJobDefinition,
    payload,
) -> None:
    body = payload()
    resource_id = str(body.deploy_job_id if hasattr(body, "deploy_job_id") else body.solution_id)

    job, _ = await service.enqueue_platform_job(
        db_session,
        definition,
        body,
        dedupe_key=resource_id,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="solution",
        resource_id=resource_id,
        title="Solution build",
        action_url=None,
    )

    assert job.execution_backend == "local"


@pytest.mark.asyncio
async def test_enqueue_keeps_default_class_jobs_local_when_isolation_is_on(
    db_session: AsyncSession,
) -> None:
    await _isolate_jobs(db_session)

    job = await _enqueue(db_session)

    assert job.execution_backend == "local"


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


def test_requires_action_status_is_terminal_and_not_active() -> None:
    assert "requires_action" in service.TERMINAL_PLATFORM_JOB_STATUSES
    assert "requires_action" not in service.ACTIVE_PLATFORM_JOB_STATUSES
    assert PlatformJobStatus.REQUIRES_ACTION.value == "requires_action"


@pytest.mark.asyncio
async def test_handler_requires_action_finishes_with_result_and_releases_lease(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    job.lease_owner = "test-runner"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    async def requires_confirmation(*_args):
        raise PlatformJobRequiresAction(
            phase="Confirm deletes",
            result={"requires_action": "confirm_deletes", "pending_deletes": []},
        )

    definition = PlatformJobDefinition(
        job_type=job.job_type,
        payload_version=job.payload_version,
        payload_model=APPLICATION_PUBLISH_DEFINITION.payload_model,
        handler=requires_confirmation,
        policy=PlatformJobPolicy(timeout_seconds=30),
    )
    published = AsyncMock()
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _: definition)
    monkeypatch.setattr(service, "publish_platform_job_update", published)

    assert await runner.run_claimed_platform_job(job.id, token) is True

    await db_session.refresh(job)
    assert job.status == "requires_action"
    assert job.phase == "Confirm deletes"
    assert job.result == {"requires_action": "confirm_deletes", "pending_deletes": []}
    assert job.completed_at is not None
    assert job.lease_owner is None
    assert job.lease_token is None
    assert job.heartbeat_at is None
    assert job.lease_expires_at is None
    assert published.await_count == 1

    cancelled, accepted = await service.request_platform_job_cancel(db_session, job)
    assert cancelled.id == job.id
    assert accepted is False

    replacement, reused = await service.enqueue_platform_job(
        db_session,
        APPLICATION_PUBLISH_DEFINITION,
        ApplicationPublishPayload(application_id=uuid4()),
        dedupe_key=job.dedupe_key,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="application",
        resource_id=str(uuid4()),
        title="Replacement",
        action_url=None,
    )
    assert reused is False
    assert replacement.id != job.id


@pytest.mark.asyncio
async def test_handler_failure_persists_a_durable_result(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed git publication retains its server-owned retry-plan result."""
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    job.lease_owner = "test-runner"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    async def publication_failed(*_args):
        raise PlatformJobFailure(
            "git_operation_failed",
            "Push failed",
            retryable=True,
            result={"sync_result": {"retryable": True, "retry_plan": {"db_applied": True}}},
        )

    definition = PlatformJobDefinition(
        job_type=job.job_type,
        payload_version=job.payload_version,
        payload_model=APPLICATION_PUBLISH_DEFINITION.payload_model,
        handler=publication_failed,
        policy=PlatformJobPolicy(timeout_seconds=30),
    )
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _: definition)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert await runner.run_claimed_platform_job(job.id, token) is True

    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_retryable is True
    assert job.result == {
        "sync_result": {"retryable": True, "retry_plan": {"db_applied": True}}
    }

@pytest.mark.asyncio
async def test_requires_action_phase_is_bounded_to_two_hundred_characters(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    await db_session.commit()

    @asynccontextmanager
    async def test_context() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    monkeypatch.setattr(service, "get_db_context", test_context)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert await service.finish_platform_job(
        job.id,
        token,
        status="requires_action",
        phase="x" * 201,
        result={"requires_action": "confirm_deletes"},
    )

    await db_session.refresh(job)
    assert job.phase == "x" * 200


@pytest.mark.asyncio
async def test_requires_action_projects_result_to_public_job_and_notification(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.status = "requires_action"
    job.phase = "Confirm deletes"
    job.result = {"requires_action": "confirm_deletes"}
    job.notification_id = uuid4()
    notifications = MagicMock()
    notifications.update_notification = AsyncMock()
    monkeypatch.setattr(service, "get_notification_service", lambda: notifications)
    monkeypatch.setattr(service.pubsub_manager, "broadcast", AsyncMock())

    await service.publish_platform_job_update(job)

    public = service.platform_job_to_public(job)
    assert public.status is PlatformJobStatus.REQUIRES_ACTION
    assert public.result == {"requires_action": "confirm_deletes"}
    update = notifications.update_notification.await_args.args[1]
    assert update.status.value == "awaiting_action"
    assert update.result == {
        "job_id": str(job.id),
        "requires_action": "confirm_deletes",
    }


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

    assert broadcast.await_count == 2
    channels = {call.args[0] for call in broadcast.await_args_list}
    assert channels == {
        f"notification:{job.requested_by_user_id}",
        "notification:admins",
    }
    for call in broadcast.await_args_list:
        event = call.args[1]
        assert event["type"] == "platform_job_updated"
        assert event["job"] == service.platform_job_to_public(job).model_dump(
            mode="json"
        )
        assert "payload" not in event["job"]
        assert "requested_by_email" not in event["job"]


@pytest.mark.parametrize("job_type", sorted(APP_BUILD_JOB_TYPES))
async def test_build_oom_message_reaches_notification_and_public_job(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, job_type: str,
) -> None:
    job = await _enqueue(db_session)
    job.job_type = job_type
    job.status = "failed"
    job.error_code = "memory_pressure"
    job.error_message = app_build_memory_message()
    job.notification_id = uuid4()
    notifications = MagicMock()
    notifications.update_notification = AsyncMock()
    broadcast = AsyncMock()
    monkeypatch.setattr(service, "get_notification_service", lambda: notifications)
    monkeypatch.setattr(service.pubsub_manager, "broadcast", broadcast)

    await service.publish_platform_job_update(job)

    update = notifications.update_notification.await_args.args[1]
    assert update.error == app_build_memory_message()
    assert update.status.value == "failed"
    for call in broadcast.await_args_list:
        assert call.args[1]["job"]["error"]["message"] == app_build_memory_message()


async def test_python_build_memory_error_is_recorded_with_recovery(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.status = "running"
    job.lease_token = uuid4()
    token = job.lease_token
    await db_session.commit()

    async def allocation_failed(*_args):
        raise MemoryError

    definition = PlatformJobDefinition(
        job_type=job.job_type,
        payload_version=job.payload_version,
        payload_model=APPLICATION_PUBLISH_DEFINITION.payload_model,
        handler=allocation_failed,
        policy=PlatformJobPolicy(timeout_seconds=30),
    )
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _: definition)
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    await runner.run_claimed_platform_job(job.id, token)
    await db_session.refresh(job)
    assert job.error_code == "memory_pressure"
    assert job.error_message == app_build_memory_message()


@pytest.mark.asyncio
async def test_application_publish_preserves_bundle_oom_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = uuid4()

    @asynccontextmanager
    async def fake_db_context():
        yield SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(id=app_id, organization_id=None)
            )
        )

    class Repository:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def publish(self, *_args, **_kwargs):
            raise ValueError(
                "Bundle build failed during publish: " + app_build_memory_message()
            )

    monkeypatch.setattr(
        "src.jobs.platform.application_publish.get_db_context", fake_db_context
    )
    monkeypatch.setattr(
        "src.jobs.platform.application_publish.ApplicationRepository", Repository
    )

    with pytest.raises(PlatformJobFailure) as failure:
        await run_application_publish(
            SimpleNamespace(
                requested_by_user_id=str(uuid4()),
                requested_by_email="dev@example.test",
                requested_by_name="Dev",
                report=AsyncMock(),
            ),
            ApplicationPublishPayload(application_id=app_id),
        )

    assert failure.value.code == "application_publish_failed"
    assert failure.value.retryable is False
    assert failure.value.message == (
        "Bundle build failed during publish: " + app_build_memory_message()
    )


@pytest.mark.asyncio
async def test_notification_projection_is_persisted_before_websocket_broadcasts(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await _enqueue(db_session)
    job.notification_id = uuid4()
    events: list[str] = []
    notifications = MagicMock()
    notifications.update_notification = AsyncMock(
        side_effect=lambda *_args, **_kwargs: events.append("notification")
    )
    monkeypatch.setattr(service, "get_notification_service", lambda: notifications)
    monkeypatch.setattr(
        service.pubsub_manager,
        "broadcast",
        AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("broadcast")),
    )

    await service.publish_platform_job_update(job)

    assert events == ["notification", "broadcast", "broadcast"]


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


@pytest.mark.asyncio
async def test_checkpoint_with_progress_sets_percent_in_same_write(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    await db_session.commit()
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert await service.checkpoint_platform_job(
        job.id, token, result={"moved": 1}, phase="Moving 2 of 4", current=1, total=4
    )
    await db_session.refresh(job)
    assert (job.result, job.phase) == ({"moved": 1}, "Moving 2 of 4")
    assert (job.progress_current, job.progress_total, job.progress_percent) == (1, 4, 25.0)

    assert await service.checkpoint_platform_job(job.id, token, result={"moved": 2}, phase="Later")
    await db_session.refresh(job)
    assert (job.progress_current, job.progress_total, job.progress_percent) == (1, 4, 25.0)


@pytest.mark.asyncio
async def test_find_active_platform_job_ignores_terminal_jobs(db_session: AsyncSession) -> None:
    job = await _enqueue(db_session)
    assert (await service.find_active_platform_job(db_session, job.job_type, job.dedupe_key)).id == job.id
    job.status = "succeeded"
    await db_session.flush()
    assert await service.find_active_platform_job(db_session, job.job_type, job.dedupe_key) is None
