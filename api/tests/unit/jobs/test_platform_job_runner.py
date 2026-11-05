"""Audit identity propagation for durable platform jobs."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import BaseModel

from src.jobs.platform.base import PlatformJobDefinition, PlatformJobPolicy
from src.jobs.platform.runner import run_claimed_platform_job
from src.models.orm.platform_jobs import PlatformJob
from src.services.audit_context import current_actor


class _Payload(BaseModel):
    value: str


def _running_job(*, payload_version: int, payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        lease_token=uuid4(),
        status="running",
        job_type="application.sdk_update",
        payload_version=payload_version,
        payload=payload,
        encrypted_payload=None,
        organization_id=None,
        requested_by_user_id=str(uuid4()),
        requested_by_email="operator@example.com",
        requested_by_name="Operator",
        impersonated_by_user_id=None,
        impersonation_session_id=None,
        result=None,
    )


@pytest.mark.asyncio
async def test_runner_restores_requesting_actor_for_handler(monkeypatch) -> None:
    job_id = uuid4()
    lease_token = uuid4()
    organization_id = uuid4()
    user_id = uuid4()
    operator_id = uuid4()
    impersonation_session_id = uuid4()
    job = SimpleNamespace(
        id=job_id,
        lease_token=lease_token,
        status="running",
        job_type="test.actor",
        payload_version=1,
        payload={"value": "ok"},
        encrypted_payload=None,
        organization_id=organization_id,
        requested_by_user_id=str(user_id),
        requested_by_email="operator@example.com",
        requested_by_name="Operator",
        impersonated_by_user_id=operator_id,
        impersonation_session_id=impersonation_session_id,
        result=None,
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db.execute.return_value = result

    @asynccontextmanager
    async def db_context():
        yield db

    async def handler(_context, _payload):
        actor = current_actor()
        assert actor is not None
        assert actor.user_id == user_id
        assert actor.organization_id == organization_id
        assert actor.email == "operator@example.com"
        assert actor.source == "platform_job"
        assert actor.impersonated_by_user_id == operator_id
        assert actor.impersonation_session_id == impersonation_session_id
        return {"ok": True}

    definition = PlatformJobDefinition(
        job_type="test.actor",
        payload_version=1,
        payload_model=_Payload,
        handler=handler,
        policy=PlatformJobPolicy(timeout_seconds=30),
    )
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr("src.jobs.platform.runner.get_db_context", db_context)
    monkeypatch.setattr(
        "src.jobs.platform.runner.get_platform_job_definition",
        lambda _job_type: definition,
    )
    monkeypatch.setattr("src.jobs.platform.runner.finish_platform_job", finish)

    assert await run_claimed_platform_job(job_id, lease_token)
    assert current_actor() is None
    finish.assert_awaited_once_with(
        job_id, lease_token, status="succeeded", result={"ok": True}
    )


@pytest.mark.asyncio
async def test_old_sdk_update_runner_refuses_pinned_upgrade_payload(monkeypatch) -> None:
    """A pre-pinning worker must never silently ignore the new target fields."""
    from src.jobs.platform import runner

    job_id, lease_token = uuid4(), uuid4()
    job = SimpleNamespace(
        id=job_id, status="running", job_type="application.sdk_update",
        payload_version=2,
    )
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = job

    @asynccontextmanager
    async def context():
        yield db

    handler = AsyncMock(side_effect=AssertionError("old SDK handler executed"))
    monkeypatch.setattr(runner, "get_db_context", context)
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _kind: SimpleNamespace(
        handler=handler,
        payload_model_for_version=lambda _version: None,
    ))
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr(runner, "finish_platform_job", finish)

    assert not await runner.run_claimed_platform_job(job_id, lease_token)
    handler.assert_not_awaited()
    assert finish.await_args.kwargs["error_code"] == "unsupported_payload_version"


@pytest.mark.asyncio
async def test_runner_dispatches_persisted_legacy_sdk_update_payload(
    db_session, monkeypatch
) -> None:
    from shared.models import ApplicationSdkUpdatePayloadV1
    from src.jobs.platform.application_sdk_update import ApplicationSdkUpdatePayload

    job_id, lease_token, application_id = uuid4(), uuid4(), uuid4()
    job = PlatformJob(
        id=job_id,
        job_type="application.sdk_update",
        payload_version=1,
        payload={"application_id": str(application_id)},
        requested_by_user_id=str(uuid4()),
        requested_by_email="operator@example.com",
        requested_by_name="Operator",
        title="Updating App SDK",
        status="running",
        lease_token=lease_token,
    )
    db_session.add(job)
    await db_session.flush()

    @asynccontextmanager
    async def db_context():
        yield db_session

    handled: list[ApplicationSdkUpdatePayloadV1] = []

    async def handler(_context, payload):
        handled.append(payload)
        return {"application_id": str(payload.application_id)}

    definition = PlatformJobDefinition(
        job_type="application.sdk_update",
        payload_version=2,
        payload_model=ApplicationSdkUpdatePayload,
        handler=handler,
        policy=PlatformJobPolicy(timeout_seconds=30),
        historical_payload_models={1: ApplicationSdkUpdatePayloadV1},
    )
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr("src.jobs.platform.runner.get_db_context", db_context)
    monkeypatch.setattr(
        "src.jobs.platform.runner.get_platform_job_definition",
        lambda _job_type: definition,
    )
    monkeypatch.setattr("src.jobs.platform.runner.finish_platform_job", finish)

    assert await run_claimed_platform_job(job_id, lease_token)
    assert len(handled) == 1
    assert isinstance(handled[0], ApplicationSdkUpdatePayloadV1)
    assert handled[0].application_id == application_id
    finish.assert_awaited_once_with(
        job_id,
        lease_token,
        status="succeeded",
        result={"application_id": str(application_id)},
    )


@pytest.mark.asyncio
async def test_runner_rejects_automatic_fields_on_legacy_sdk_update_payload(
    monkeypatch,
) -> None:
    from shared.models import ApplicationSdkUpdatePayloadV1
    from src.jobs.platform import runner
    from src.jobs.platform.application_sdk_update import ApplicationSdkUpdatePayload

    job = _running_job(
        payload_version=1,
        payload={"application_id": str(uuid4()), "automatic_upgrade": True},
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db.execute.return_value = result

    @asynccontextmanager
    async def db_context():
        yield db

    handler = AsyncMock()
    definition = PlatformJobDefinition(
        job_type="application.sdk_update",
        payload_version=2,
        payload_model=ApplicationSdkUpdatePayload,
        handler=handler,
        policy=PlatformJobPolicy(timeout_seconds=30),
        historical_payload_models={1: ApplicationSdkUpdatePayloadV1},
    )
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr(runner, "get_db_context", db_context)
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _kind: definition)
    monkeypatch.setattr(runner, "finish_platform_job", finish)

    assert await runner.run_claimed_platform_job(job.id, job.lease_token)
    handler.assert_not_awaited()
    assert finish.await_args.kwargs["error_code"] == "invalid_payload"


@pytest.mark.asyncio
async def test_runner_uses_legacy_model_for_encrypted_sdk_update_payload(
    monkeypatch,
) -> None:
    from shared.models import ApplicationSdkUpdatePayloadV1
    from src.jobs.platform import runner
    from src.jobs.platform.application_sdk_update import ApplicationSdkUpdatePayload

    application_id = uuid4()
    job = _running_job(payload_version=1, payload={"protected": True})
    job.encrypted_payload = "encrypted"
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db.execute.return_value = result

    @asynccontextmanager
    async def db_context():
        yield db

    handled: list[ApplicationSdkUpdatePayloadV1] = []

    async def handler(_context, payload):
        handled.append(payload)
        return {}

    definition = PlatformJobDefinition(
        job_type="application.sdk_update",
        payload_version=2,
        payload_model=ApplicationSdkUpdatePayload,
        handler=handler,
        policy=PlatformJobPolicy(timeout_seconds=30),
        historical_payload_models={1: ApplicationSdkUpdatePayloadV1},
    )
    monkeypatch.setattr(runner, "get_db_context", db_context)
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _kind: definition)
    monkeypatch.setattr(
        "src.core.security.decrypt_secret",
        lambda _value: f'{{"application_id": "{application_id}"}}',
    )
    monkeypatch.setattr(runner, "finish_platform_job", AsyncMock(return_value=True))

    assert await runner.run_claimed_platform_job(job.id, job.lease_token)
    assert len(handled) == 1
    assert isinstance(handled[0], ApplicationSdkUpdatePayloadV1)
    assert handled[0].application_id == application_id


@pytest.mark.asyncio
async def test_runner_refuses_unknown_payload_version(monkeypatch) -> None:
    from shared.models import ApplicationSdkUpdatePayloadV1
    from src.jobs.platform import runner
    from src.jobs.platform.application_sdk_update import ApplicationSdkUpdatePayload

    job = _running_job(payload_version=99, payload={"application_id": str(uuid4())})
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db.execute.return_value = result

    @asynccontextmanager
    async def db_context():
        yield db

    handler = AsyncMock(side_effect=AssertionError("handler executed"))
    definition = PlatformJobDefinition(
        job_type="application.sdk_update",
        payload_version=2,
        payload_model=ApplicationSdkUpdatePayload,
        handler=handler,
        policy=PlatformJobPolicy(timeout_seconds=30),
        historical_payload_models={1: ApplicationSdkUpdatePayloadV1},
    )
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr(runner, "get_db_context", db_context)
    monkeypatch.setattr(runner, "get_platform_job_definition", lambda _kind: definition)
    monkeypatch.setattr(runner, "finish_platform_job", finish)

    assert not await runner.run_claimed_platform_job(job.id, job.lease_token)
    handler.assert_not_awaited()
    assert finish.await_args.kwargs["error_code"] == "unsupported_payload_version"
