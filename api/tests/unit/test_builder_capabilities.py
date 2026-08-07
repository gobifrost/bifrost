from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.authorization_scopes import SOLUTION_BUILD_JOBS_EXECUTE_SCOPE
from src.core.security import decode_token
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.services.builder.capabilities import (
    ACTOR_TYPE_BUILD_CAPABILITY,
    mint_build_capability,
    require_build_capability,
)


def _job() -> SolutionBuildJob:
    return SolutionBuildJob(
        id=uuid4(),
        solution_id=uuid4(),
        app_id=uuid4(),
        requested_by=uuid4(),
        source_sha256="a" * 64,
        toolchain_version="test",
    )


@pytest.mark.asyncio
async def test_build_capability_is_bound_to_one_job() -> None:
    job = _job()
    token = mint_build_capability(job)
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    assert payload["actor_type"] == ACTOR_TYPE_BUILD_CAPABILITY
    assert payload["job_id"] == str(job.id)
    assert payload["scopes"] == [SOLUTION_BUILD_JOBS_EXECUTE_SCOPE]

    accepted = await require_build_capability(job.id, f"Bearer {token}")
    assert accepted["solution_id"] == str(job.solution_id)

    with pytest.raises(HTTPException) as exc:
        await require_build_capability(uuid4(), f"Bearer {token}")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_build_capability_rejects_missing_and_normal_tokens() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_build_capability(uuid4(), None)
    assert exc.value.status_code == 403
