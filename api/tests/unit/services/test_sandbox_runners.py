from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.sandbox_runners import dispatch_sandbox_platform_job


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
        {"job_id": str(job.id), "dispatch_attempt": 2},
        priority=9,
    )
