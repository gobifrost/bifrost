"""Admin Builder runner setup route tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from src.models.contracts.platform_jobs import PlatformJobStatus
from src.models.contracts.sandbox_runner import (
    SandboxRunnerCloudflareConfig,
    SandboxRunnerConfigPublic,
    SandboxRunnerConfigSave,
    SandboxRunnerReadiness,
)
from src.routers.sandbox_runner_admin import (
    _require_no_active_sandbox_work,
    get_runner_setup,
    provision_runner,
    save_runner_setup,
)


def _user() -> SimpleNamespace:
    user_id = uuid4()
    return SimpleNamespace(
        user_id=user_id,
        id=user_id,
        email="admin@example.com",
        name="Platform Admin",
    )


def _config() -> SandboxRunnerConfigPublic:
    return SandboxRunnerConfigPublic(
        provider="cloudflare",
        enabled=False,
        callback_base_url="https://bifrost.example.com",
        provisioned=False,
        connected=False,
    )


@pytest.mark.asyncio
async def test_get_runner_setup_returns_masked_config_and_recommended_origin():
    db = AsyncMock()
    active_job_id = uuid4()
    db.scalar.return_value = active_job_id
    request = SimpleNamespace(base_url="https://request.example.com/")
    service = AsyncMock()
    service.get_config.return_value = _config()
    readiness = SandboxRunnerReadiness(
        configured=True,
        ready=False,
        ai_configured=True,
        provider="cloudflare",
    )

    with (
        patch(
            "src.routers.sandbox_runner_admin.SandboxRunnerConfigService",
            return_value=service,
        ),
        patch(
            "src.routers.sandbox_runner_admin.get_builder_readiness",
            AsyncMock(return_value=(True, readiness)),
        ),
        patch(
            "src.routers.sandbox_runner_admin.get_settings",
            return_value=SimpleNamespace(public_url=""),
        ),
        patch(
            "src.routers.sandbox_runner_admin.configured_runner_image",
            return_value="ghcr.io/gobifrost/bifrost-builder-runner:dev",
        ),
    ):
        state = await get_runner_setup(request, db, _user())

    assert state.config == _config()
    assert state.recommended_callback_base_url == "https://request.example.com"
    assert state.cloudflare_permissions == ["Workers Scripts Write"]
    assert state.runner_image.endswith(":dev")
    assert state.active_provisioning_job_id == active_job_id


@pytest.mark.asyncio
async def test_save_runner_setup_commits_encrypted_service_result():
    db = AsyncMock()
    service = AsyncMock()
    service.save_config.return_value = _config()
    body = SandboxRunnerConfigSave(
        provider="cloudflare",
        callback_base_url="https://bifrost.example.com",
        cloudflare=SandboxRunnerCloudflareConfig(
            account_id="account-id",
            api_token="write-only-token",
        ),
    )

    with (
        patch(
            "src.routers.sandbox_runner_admin._require_no_active_sandbox_work",
            AsyncMock(),
        ),
        patch(
            "src.routers.sandbox_runner_admin.SandboxRunnerConfigService",
            return_value=service,
        ),
    ):
        saved = await save_runner_setup(body, db, _user())

    assert saved == _config()
    service.save_config.assert_awaited_once()
    assert service.save_config.await_args.kwargs["updated_by"] == "admin@example.com"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_active_builder_work_blocks_runner_reconfiguration():
    db = AsyncMock()
    db.scalar.return_value = 1

    with pytest.raises(HTTPException) as raised:
        await _require_no_active_sandbox_work(db)

    assert raised.value.status_code == 409


@pytest.mark.asyncio
async def test_provision_runner_returns_durable_job_location():
    db = AsyncMock()
    response = Response()
    service = AsyncMock()
    service.get_config.return_value = _config()
    job = SimpleNamespace(
        id=uuid4(),
        status="queued",
        notification_id=uuid4(),
    )
    readiness = SandboxRunnerReadiness(
        configured=True,
        ready=False,
        ai_configured=True,
        provider="cloudflare",
        credentials_configured=True,
        callback_configured=True,
    )
    enqueue = AsyncMock(return_value=(job, False))

    with (
        patch(
            "src.routers.sandbox_runner_admin.SandboxRunnerConfigService",
            return_value=service,
        ),
        patch(
            "src.routers.sandbox_runner_admin.get_builder_readiness",
            AsyncMock(return_value=(True, readiness)),
        ),
        patch(
            "src.routers.sandbox_runner_admin.enqueue_platform_job",
            enqueue,
        ),
        patch(
            "src.routers.sandbox_runner_admin.publish_platform_job_update",
            AsyncMock(),
        ),
    ):
        accepted = await provision_runner(response, db, _user())

    assert accepted.job_id == job.id
    assert accepted.status == PlatformJobStatus.QUEUED
    assert response.headers["Location"] == f"/api/platform-jobs/{job.id}"
    enqueue.assert_awaited_once()
    db.commit.assert_awaited_once()
