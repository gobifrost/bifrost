"""Short-lived, one-job capabilities for the secretless build coordinator."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Header, HTTPException, status

from shared.authorization_scopes import SOLUTION_BUILD_JOBS_EXECUTE_SCOPE
from src.core.security import create_access_token, decode_token
from src.models.orm.solution_build_jobs import SolutionBuildJob

ACTOR_TYPE_BUILD_CAPABILITY = "build_capability"


def mint_build_capability(job: SolutionBuildJob) -> str:
    """Mint authority for exactly one already-claimed build job."""
    return create_access_token(
        {
            "actor_type": ACTOR_TYPE_BUILD_CAPABILITY,
            "job_id": str(job.id),
            "solution_id": str(job.solution_id),
            "sub": str(job.requested_by) if job.requested_by else "builder",
            "jti": uuid4().hex,
            "scopes": [SOLUTION_BUILD_JOBS_EXECUTE_SCOPE],
        },
        expires_delta=timedelta(minutes=15),
    )


async def require_build_capability(
    job_id: UUID,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Verify a bearer capability and bind it to the path's exact job id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid build capability",
        )
    payload = decode_token(
        authorization.removeprefix("Bearer ").strip(),
        expected_type="access",
    )
    if (
        payload is None
        or payload.get("actor_type") != ACTOR_TYPE_BUILD_CAPABILITY
        or payload.get("job_id") != str(job_id)
        or SOLUTION_BUILD_JOBS_EXECUTE_SCOPE not in payload.get("scopes", [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid build capability",
        )
    return payload
