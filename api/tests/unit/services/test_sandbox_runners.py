from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.sandbox_runners import (
    SandboxJobEnvelope,
    dispatch_sandbox_platform_job,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_type", "expected_queue"),
    [
        ("solution.build", "solution-builds"),
        ("solution.builder.turn", "solution-builder-turns"),
    ],
)
async def test_local_dispatch_routes_job_reference_to_existing_worker(
    job_type: str,
    expected_queue: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = SimpleNamespace(
        id=uuid4(),
        job_type=job_type,
        attempt=2,
        timeout_seconds=900,
        lease_token=uuid4(),
        status="running",
        external_provider=None,
        external_run_id=None,
        external_started_at=None,
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db.execute.return_value = result
    db.scalar.return_value = job_type

    @asynccontextmanager
    async def db_context():
        yield db

    config = MagicMock()
    config.get_decrypted_internal_config = AsyncMock(
        return_value={
            "provider": "local",
            "enabled": True,
            "provisioned": True,
            "connected": True,
        }
    )
    publish = AsyncMock()
    monkeypatch.setattr("src.services.sandbox_runners.get_db_context", db_context)
    monkeypatch.setattr(
        "src.services.sandbox_runners.SandboxRunnerConfigService",
        lambda _db: config,
    )
    monkeypatch.setattr("src.jobs.rabbitmq.publish_message", publish)

    dispatched = await dispatch_sandbox_platform_job(
        job.id,
        job.lease_token,
        input_sha256="a" * 64,
    )

    assert dispatched.provider == "local"
    assert dispatched.external_run_id == f"{job.id}-2"
    publish.assert_awaited_once_with(
        expected_queue,
        {
            "job_id": str(job.id),
            "dispatch_attempt": 2,
            "input_sha256": "a" * 64,
        },
        priority=9,
    )


@pytest.mark.asyncio
async def test_cloudflare_builder_turn_dispatch_projects_two_sandbox_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = SimpleNamespace(
        id=uuid4(),
        job_type="solution.builder.turn",
        attempt=3,
        timeout_seconds=900,
        lease_token=uuid4(),
        status="running",
        external_provider=None,
        external_run_id=None,
        external_started_at=None,
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db.execute.return_value = result

    @asynccontextmanager
    async def db_context():
        yield db

    config = MagicMock()
    config.get_decrypted_internal_config = AsyncMock(
        return_value={
            "provider": "cloudflare",
            "enabled": True,
            "provisioned": True,
            "connected": True,
            "callback_base_url": "https://debug.example.test/",
            "cloudflare": {
                "account_id": "account",
                "workflow_name": "bifrost-build-workflow",
                "api_token": "token",
            },
        }
    )
    captured: dict[str, object] = {}

    async def dispatch_cloudflare(_config, envelope, *, instance_id):
        captured["envelope"] = envelope
        captured["instance_id"] = instance_id
        return "cloudflare-run-id"

    monkeypatch.setattr("src.services.sandbox_runners.get_db_context", db_context)
    monkeypatch.setattr(
        "src.services.sandbox_runners.SandboxRunnerConfigService",
        lambda _db: config,
    )
    monkeypatch.setattr(
        "src.services.sandbox_runners.mint_sandbox_job_capability",
        lambda _job: "capability",
    )
    monkeypatch.setattr(
        "src.services.sandbox_runners._dispatch_cloudflare",
        dispatch_cloudflare,
    )

    dispatched = await dispatch_sandbox_platform_job(
        job.id,
        job.lease_token,
        input_sha256="a" * 64,
    )

    assert dispatched.provider == "cloudflare"
    assert captured["instance_id"] == f"{job.id}-3"
    envelope = captured["envelope"]
    assert isinstance(envelope, SandboxJobEnvelope)
    assert envelope.runner_sandbox_id == f"bifrost-{job.id}-3-runner"
    assert envelope.workspace_sandbox_id == f"bifrost-{job.id}-3-workspace"
    assert envelope.workspace_broker_url == "http://workspace.bifrost.internal"
    assert envelope.workspace_allowed_hosts == []


@pytest.mark.asyncio
async def test_cloudflare_app_build_dispatch_uses_build_only_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = SimpleNamespace(
        id=uuid4(),
        job_type="solution.build",
        attempt=1,
        timeout_seconds=900,
        lease_token=uuid4(),
        status="running",
        external_provider=None,
        external_run_id=None,
        external_started_at=None,
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db.execute.return_value = result

    @asynccontextmanager
    async def db_context():
        yield db

    config = MagicMock()
    config.get_decrypted_internal_config = AsyncMock(
        return_value={
            "provider": "cloudflare",
            "enabled": True,
            "provisioned": True,
            "connected": True,
            "callback_base_url": "https://debug.example.test",
            "cloudflare": {
                "account_id": "account",
                "workflow_name": "bifrost-build-workflow",
                "api_token": "token",
            },
        }
    )
    captured: dict[str, object] = {}

    async def dispatch_cloudflare(_config, envelope, *, instance_id):
        captured["envelope"] = envelope
        captured["instance_id"] = instance_id
        return "cloudflare-build-run-id"

    monkeypatch.setattr("src.services.sandbox_runners.get_db_context", db_context)
    monkeypatch.setattr(
        "src.services.sandbox_runners.SandboxRunnerConfigService",
        lambda _db: config,
    )
    monkeypatch.setattr(
        "src.services.sandbox_runners.mint_sandbox_job_capability",
        lambda _job: "capability",
    )
    monkeypatch.setattr(
        "src.services.sandbox_runners._dispatch_cloudflare",
        dispatch_cloudflare,
    )

    dispatched = await dispatch_sandbox_platform_job(
        job.id,
        job.lease_token,
        input_sha256="a" * 64,
    )

    assert dispatched.provider == "cloudflare"
    assert captured["instance_id"] == f"{job.id}-1"
    envelope = captured["envelope"]
    assert isinstance(envelope, SandboxJobEnvelope)
    assert envelope.job_type == "solution.build"
    assert envelope.runner_sandbox_id is None
    assert envelope.workspace_sandbox_id is None
    assert envelope.workspace_broker_url is None
    assert not hasattr(envelope, "llm_config")
