"""Audit identity propagation for durable platform jobs."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import BaseModel

from src.jobs.platform.base import PlatformJobDefinition, PlatformJobPolicy
from src.jobs.platform.runner import run_claimed_platform_job
from src.services.audit_context import current_actor


class _Payload(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_runner_restores_requesting_actor_for_handler(monkeypatch) -> None:
    job_id = uuid4()
    lease_token = uuid4()
    organization_id = uuid4()
    user_id = uuid4()
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
