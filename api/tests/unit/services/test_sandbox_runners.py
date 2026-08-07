"""Provider dispatch tests for sandboxed PlatformJobs."""

from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.sandbox_runners import (
    SandboxDispatchFailed,
    SandboxRunnerUnavailable,
    cancel_external_sandbox_run,
    dispatch_sandbox_platform_job,
)


def _job(*, attempt: int = 2):
    return SimpleNamespace(
        id=uuid4(),
        job_type="solution.build",
        attempt=attempt,
        timeout_seconds=900,
        lease_token=uuid4(),
        status="running",
    )


def _db_context(job):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db.execute.return_value = result

    @asynccontextmanager
    async def context():
        yield db

    return context


def _http_client(response_body: dict):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = response_body
    client = AsyncMock()
    client.post.return_value = response
    client.patch.return_value = response
    client.delete.return_value = response
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=client)
    manager.__aexit__ = AsyncMock(return_value=None)
    return manager, client


@pytest.mark.asyncio
async def test_cloudflare_dispatch_uses_attempt_id_and_json_encoded_params():
    job = _job(attempt=3)
    manager, client = _http_client(
        {"success": True, "result": {"id": "workflow-run-123"}}
    )
    config = {
        "provider": "cloudflare",
        "enabled": True,
        "provisioned": True,
        "connected": True,
        "callback_base_url": "https://bifrost.example.com/",
        "cloudflare": {
            "account_id": "account-123",
            "workflow_name": "bifrost-builder-workflow",
            "api_token": "cf-secret",
        },
    }
    config_service = MagicMock()
    config_service.get_decrypted_internal_config = AsyncMock(return_value=config)

    with (
        patch("src.services.sandbox_runners.get_db_context", _db_context(job)),
        patch(
            "src.services.sandbox_runners.SandboxRunnerConfigService",
            return_value=config_service,
        ),
        patch(
            "src.services.sandbox_runners.mint_sandbox_job_capability",
            return_value="job-capability",
        ),
        patch("src.services.sandbox_runners.httpx.AsyncClient", return_value=manager),
    ):
        result = await dispatch_sandbox_platform_job(
            job.id,
            job.lease_token,
            input_sha256="a" * 64,
        )

    assert result.provider == "cloudflare"
    assert result.external_run_id == "workflow-run-123"
    request = client.post.await_args
    assert request.args[0].endswith(
        "/accounts/account-123/workflows/bifrost-builder-workflow/instances"
    )
    assert request.kwargs["headers"] == {"Authorization": "Bearer cf-secret"}
    assert request.kwargs["json"]["instance_id"] == f"{job.id}-3"
    params = json.loads(request.kwargs["json"]["params"])
    assert params == {
        "schema_version": 1,
        "job_id": str(job.id),
        "job_type": "solution.build",
        "dispatch_attempt": 3,
        "callback_base_url": "https://bifrost.example.com",
        "capability": "job-capability",
        "input_sha256": "a" * 64,
        "timeout_seconds": 900,
    }
    assert "cf-secret" not in request.kwargs["json"]["params"]


@pytest.mark.asyncio
async def test_local_dispatch_uses_same_envelope_contract():
    job = _job()
    manager, client = _http_client({"run_id": "local-run-1"})
    config_service = MagicMock()
    config_service.get_decrypted_internal_config = AsyncMock(
        return_value={
            "provider": "local",
            "enabled": True,
            "provisioned": True,
            "connected": True,
            "callback_base_url": "http://localhost:3000",
            "local": {
                "endpoint_url": "http://runner:9137/",
                "runner_secret": "local-secret",
            },
        }
    )

    with (
        patch("src.services.sandbox_runners.get_db_context", _db_context(job)),
        patch(
            "src.services.sandbox_runners.SandboxRunnerConfigService",
            return_value=config_service,
        ),
        patch(
            "src.services.sandbox_runners.mint_sandbox_job_capability",
            return_value="job-capability",
        ),
        patch("src.services.sandbox_runners.httpx.AsyncClient", return_value=manager),
    ):
        result = await dispatch_sandbox_platform_job(
            job.id,
            job.lease_token,
            input_sha256="b" * 64,
        )

    assert result.provider == "local"
    assert result.external_run_id == "local-run-1"
    request = client.post.await_args
    assert request.args[0] == "http://runner:9137/jobs"
    assert request.kwargs["headers"] == {"Authorization": "Bearer local-secret"}
    assert request.kwargs["json"]["instance_id"] == f"{job.id}-2"
    assert request.kwargs["json"]["job"]["job_type"] == "solution.build"


@pytest.mark.asyncio
async def test_dispatch_refuses_disabled_provider_without_http_request():
    job = _job()
    config_service = MagicMock()
    config_service.get_decrypted_internal_config = AsyncMock(
        return_value={"provider": "cloudflare", "enabled": False}
    )
    http_factory = MagicMock()

    with (
        patch("src.services.sandbox_runners.get_db_context", _db_context(job)),
        patch(
            "src.services.sandbox_runners.SandboxRunnerConfigService",
            return_value=config_service,
        ),
        patch("src.services.sandbox_runners.httpx.AsyncClient", http_factory),
    ):
        with pytest.raises(SandboxRunnerUnavailable, match="not enabled"):
            await dispatch_sandbox_platform_job(
                job.id,
                job.lease_token,
                input_sha256="c" * 64,
            )

    http_factory.assert_not_called()


@pytest.mark.asyncio
async def test_cloudflare_dispatch_rejects_malformed_success_response():
    job = _job()
    manager, _client = _http_client({"success": True, "result": {}})
    config_service = MagicMock()
    config_service.get_decrypted_internal_config = AsyncMock(
        return_value={
            "provider": "cloudflare",
            "enabled": True,
            "provisioned": True,
            "connected": True,
            "callback_base_url": "https://bifrost.example.com",
            "cloudflare": {
                "account_id": "account-123",
                "workflow_name": "workflow",
                "api_token": "token",
            },
        }
    )

    with (
        patch("src.services.sandbox_runners.get_db_context", _db_context(job)),
        patch(
            "src.services.sandbox_runners.SandboxRunnerConfigService",
            return_value=config_service,
        ),
        patch(
            "src.services.sandbox_runners.mint_sandbox_job_capability",
            return_value="job-capability",
        ),
        patch("src.services.sandbox_runners.httpx.AsyncClient", return_value=manager),
    ):
        with pytest.raises(SandboxDispatchFailed, match="invalid workflow response"):
            await dispatch_sandbox_platform_job(
                job.id,
                job.lease_token,
                input_sha256="d" * 64,
            )


@pytest.mark.asyncio
async def test_cloudflare_cancel_terminates_exact_external_run():
    job = _job()
    job.external_provider = "cloudflare"
    job.external_run_id = "workflow-run-123"
    manager, client = _http_client({"success": True})
    config_service = MagicMock()
    config_service.get_decrypted_internal_config = AsyncMock(
        return_value={
            "provider": "cloudflare",
            "cloudflare": {
                "account_id": "account-123",
                "workflow_name": "workflow",
                "api_token": "token",
            },
        }
    )

    with (
        patch("src.services.sandbox_runners.get_db_context", _db_context(job)),
        patch(
            "src.services.sandbox_runners.SandboxRunnerConfigService",
            return_value=config_service,
        ),
        patch("src.services.sandbox_runners.httpx.AsyncClient", return_value=manager),
    ):
        cancelled = await cancel_external_sandbox_run(job)

    assert cancelled is True
    request = client.patch.await_args
    assert request.args[0].endswith(
        "/workflows/workflow/instances/workflow-run-123/status"
    )
    assert request.kwargs["json"] == {"status": "terminate", "rollback": False}
