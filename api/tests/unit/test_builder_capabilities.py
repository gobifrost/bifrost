from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import create_access_token, decode_token
from src.models.orm.platform_jobs import PlatformJob
from src.services.builder.capabilities import (
    ACTOR_TYPE_SANDBOX_JOB,
    SANDBOX_ARTIFACT_WRITE,
    SANDBOX_INPUT_READ,
    SANDBOX_JOB_OPERATIONS,
    decode_sandbox_job_capability,
    mint_sandbox_job_capability,
    require_sandbox_job_capability,
)


def _job() -> PlatformJob:
    return PlatformJob(
        id=uuid4(),
        job_type="solution.build",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="builder@example.com",
        requested_by_name="Builder",
        title="Build app",
        status="running",
        attempt=2,
        lease_token=uuid4(),
        timeout_seconds=600,
    )


def test_sandbox_capability_is_an_actor_contract_without_role_scopes() -> None:
    job = _job()
    token = mint_sandbox_job_capability(job)
    payload = decode_token(token, expected_type="access")

    assert payload is not None
    assert payload["actor_type"] == ACTOR_TYPE_SANDBOX_JOB
    assert payload["job_id"] == str(job.id)
    assert payload["job_type"] == "solution.build"
    assert payload["dispatch_attempt"] == 2
    assert set(payload["operations"]) == SANDBOX_JOB_OPERATIONS["solution.build"]
    assert "scopes" not in payload

    accepted = decode_sandbox_job_capability(job.id, f"Bearer {token}")
    assert accepted.dispatch_attempt == 2
    accepted.require(SANDBOX_ARTIFACT_WRITE)

    with pytest.raises(HTTPException) as exc:
        decode_sandbox_job_capability(uuid4(), f"Bearer {token}")
    assert exc.value.status_code == 403


def test_sandbox_capability_can_be_attenuated_but_not_widened() -> None:
    job = _job()
    token = mint_sandbox_job_capability(job, operations={SANDBOX_INPUT_READ})
    capability = decode_sandbox_job_capability(job.id, f"Bearer {token}")
    assert capability.operations == {SANDBOX_INPUT_READ}

    with pytest.raises(HTTPException):
        capability.require(SANDBOX_ARTIFACT_WRITE)
    with pytest.raises(ValueError, match="unsupported operations"):
        mint_sandbox_job_capability(job, operations={"platform.admin"})


@pytest.mark.asyncio
async def test_sandbox_capability_is_fenced_by_current_attempt(
    db_session: AsyncSession,
) -> None:
    job = _job()
    db_session.add(job)
    await db_session.flush()
    token = mint_sandbox_job_capability(job)

    accepted = await require_sandbox_job_capability(
        job.id,
        db_session,
        f"Bearer {token}",
    )
    assert accepted.job_id == job.id

    job.attempt += 1
    await db_session.flush()
    with pytest.raises(HTTPException) as exc:
        await require_sandbox_job_capability(
            job.id,
            db_session,
            f"Bearer {token}",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_sandbox_capability_rejects_terminal_or_normal_token(
    db_session: AsyncSession,
) -> None:
    job = _job()
    db_session.add(job)
    await db_session.flush()
    token = mint_sandbox_job_capability(job)
    job.status = "succeeded"
    await db_session.flush()

    with pytest.raises(HTTPException):
        await require_sandbox_job_capability(
            job.id,
            db_session,
            f"Bearer {token}",
        )

    normal = create_access_token({"sub": str(uuid4()), "scopes": ["*"]})
    with pytest.raises(HTTPException):
        decode_sandbox_job_capability(job.id, f"Bearer {normal}")


def test_capability_requires_a_current_leased_job() -> None:
    job = _job()
    job.status = "queued"
    job.lease_token = None
    with pytest.raises(ValueError, match="leased running job"):
        mint_sandbox_job_capability(job)
