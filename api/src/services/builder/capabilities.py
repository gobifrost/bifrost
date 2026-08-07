"""Short-lived, one-attempt capabilities for external sandbox jobs.

These credentials are deliberately not authorization scopes. Human roles grant
``solutions.build``; an external runner receives only a fixed protocol for one
already-dispatched PlatformJob attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Final, Iterable
from uuid import UUID, uuid4

from fastapi import Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import create_access_token, decode_token
from src.models.orm.platform_jobs import PlatformJob

ACTOR_TYPE_SANDBOX_JOB: Final = "sandbox_job"

SANDBOX_INPUT_READ: Final = "input.read"
SANDBOX_PROGRESS_WRITE: Final = "progress.write"
SANDBOX_CANCEL_READ: Final = "cancel.read"
SANDBOX_ARTIFACT_WRITE: Final = "artifact.write"
SANDBOX_OUTPUT_WRITE: Final = "output.write"
SANDBOX_LLM_INVOKE: Final = "llm.invoke"
SANDBOX_COMPLETE_WRITE: Final = "complete.write"

_COMMON_OPERATIONS = frozenset(
    {
        SANDBOX_INPUT_READ,
        SANDBOX_PROGRESS_WRITE,
        SANDBOX_CANCEL_READ,
        SANDBOX_COMPLETE_WRITE,
    }
)
SANDBOX_JOB_OPERATIONS: Final[dict[str, frozenset[str]]] = {
    "solution.build": _COMMON_OPERATIONS | {SANDBOX_ARTIFACT_WRITE},
    "solution.builder.turn": _COMMON_OPERATIONS
    | {SANDBOX_OUTPUT_WRITE, SANDBOX_LLM_INVOKE},
}


@dataclass(frozen=True)
class SandboxJobCapability:
    """Validated authority for one current PlatformJob dispatch attempt."""

    job_id: UUID
    job_type: str
    dispatch_attempt: int
    operations: frozenset[str]
    token_id: str

    def require(self, operation: str) -> None:
        if operation not in self.operations:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Sandbox capability does not allow this operation",
            )


def _invalid_capability() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid sandbox job capability",
    )


def mint_sandbox_job_capability(
    job: PlatformJob,
    *,
    operations: Iterable[str] | None = None,
) -> str:
    """Mint a credential for the current running attempt of ``job``."""

    allowed = SANDBOX_JOB_OPERATIONS.get(job.job_type)
    if allowed is None:
        raise ValueError(f"Platform job type {job.job_type!r} is not sandbox-executable")
    if job.status != "running" or job.attempt < 1 or job.lease_token is None:
        raise ValueError("A sandbox capability requires a leased running job")

    granted = frozenset(operations) if operations is not None else allowed
    if not granted or not granted.issubset(allowed):
        raise ValueError("Sandbox capability requested unsupported operations")

    # Keep the token alive through the external deadline plus a small callback
    # grace period. Attempt fencing invalidates it immediately on redispatch.
    lifetime_seconds = min(max(job.timeout_seconds + 10 * 60, 15 * 60), 6 * 60 * 60)
    return create_access_token(
        {
            "actor_type": ACTOR_TYPE_SANDBOX_JOB,
            "job_id": str(job.id),
            "job_type": job.job_type,
            "dispatch_attempt": job.attempt,
            "operations": sorted(granted),
            "sub": job.requested_by_user_id or "sandbox-runner",
            "jti": uuid4().hex,
        },
        expires_delta=timedelta(seconds=lifetime_seconds),
    )


def decode_sandbox_job_capability(
    job_id: UUID,
    authorization: str | None,
) -> SandboxJobCapability:
    """Decode and structurally bind a bearer credential to the path job."""

    if not authorization or not authorization.startswith("Bearer "):
        raise _invalid_capability()
    payload: dict[str, Any] | None = decode_token(
        authorization.removeprefix("Bearer ").strip(),
        expected_type="access",
    )
    try:
        if (
            payload is None
            or payload.get("actor_type") != ACTOR_TYPE_SANDBOX_JOB
            or payload.get("job_id") != str(job_id)
            or not isinstance(payload.get("job_type"), str)
            or not isinstance(payload.get("dispatch_attempt"), int)
            or not isinstance(payload.get("jti"), str)
            or not isinstance(payload.get("operations"), list)
            or not all(isinstance(item, str) for item in payload["operations"])
        ):
            raise _invalid_capability()
        operations = frozenset(payload["operations"])
        allowed = SANDBOX_JOB_OPERATIONS.get(payload["job_type"])
        if not operations or allowed is None or not operations.issubset(allowed):
            raise _invalid_capability()
        return SandboxJobCapability(
            job_id=job_id,
            job_type=payload["job_type"],
            dispatch_attempt=payload["dispatch_attempt"],
            operations=operations,
            token_id=payload["jti"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, HTTPException):
            raise
        raise _invalid_capability() from exc


async def require_sandbox_job_capability(
    job_id: UUID,
    db: AsyncSession,
    authorization: Annotated[str | None, Header()] = None,
) -> SandboxJobCapability:
    """Validate the token and fence it against the current database attempt."""

    capability = decode_sandbox_job_capability(job_id, authorization)
    job = await db.get(PlatformJob, job_id)
    if (
        job is None
        or job.job_type != capability.job_type
        or job.attempt != capability.dispatch_attempt
        or job.status not in {"running", "waiting", "cancel_requested"}
    ):
        raise _invalid_capability()
    return capability


__all__ = [
    "ACTOR_TYPE_SANDBOX_JOB",
    "SANDBOX_ARTIFACT_WRITE",
    "SANDBOX_CANCEL_READ",
    "SANDBOX_COMPLETE_WRITE",
    "SANDBOX_INPUT_READ",
    "SANDBOX_JOB_OPERATIONS",
    "SANDBOX_LLM_INVOKE",
    "SANDBOX_OUTPUT_WRITE",
    "SANDBOX_PROGRESS_WRITE",
    "SandboxJobCapability",
    "decode_sandbox_job_capability",
    "mint_sandbox_job_capability",
    "require_sandbox_job_capability",
]
