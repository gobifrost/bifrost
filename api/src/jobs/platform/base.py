"""Handler contracts for scheduler-owned platform jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import UUID

from pydantic import BaseModel

from src.jobs.platform.catalog import get_platform_job_metadata
from src.jobs.platform.policy import PlatformJobPolicy


class PlatformJobFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.result = result


class PlatformJobRequiresAction(Exception):
    """Stop a job until a user completes an explicit follow-up action."""

    def __init__(self, phase: str, result: dict[str, Any]) -> None:
        super().__init__(phase)
        self.phase = phase
        self.result = result


class PlatformJobCancelled(Exception):
    pass


class PlatformJobDeferred(Exception):
    """Release the runner while external child work completes the job."""

    def __init__(self, phase: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(phase)
        self.phase = phase
        self.result = result


@dataclass(frozen=True)
class PlatformJobContext:
    job_id: UUID
    lease_token: UUID
    organization_id: UUID | None
    requested_by_user_id: str
    requested_by_email: str
    requested_by_name: str
    impersonated_by_user_id: UUID | None = None
    impersonation_session_id: UUID | None = None
    checkpoint: dict[str, Any] | None = None

    async def report(
        self,
        phase: str,
        current: int = 0,
        total: int | None = None,
        percent: float | None = None,
    ) -> None:
        from src.services.platform_jobs import update_platform_job_progress

        updated = await update_platform_job_progress(
            self.job_id,
            self.lease_token,
            phase=phase,
            current=current,
            total=total,
            percent=percent,
        )
        if not updated:
            raise PlatformJobCancelled

    async def log(self, level: str, code: str, message: str) -> None:
        """Publish an explicitly curated, operator-safe diagnostic entry."""
        from src.services.scheduler_diagnostics import publish_system_diagnostic_log

        await publish_system_diagnostic_log(
            source="platform_job",
            level=level,
            code=code,
            message=message,
            platform_job_id=self.job_id,
        )

    async def save_checkpoint(
        self,
        result: dict[str, Any],
        *,
        phase: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        from src.services.platform_jobs import checkpoint_platform_job

        if not await checkpoint_platform_job(
            self.job_id,
            self.lease_token,
            result=result,
            phase=phase,
            current=current,
            total=total,
        ):
            raise PlatformJobCancelled


PlatformJobHandler = Callable[
    [PlatformJobContext, BaseModel],
    Awaitable[dict[str, Any] | None],
]


@dataclass(frozen=True)
class PlatformJobDefinition:
    job_type: str
    payload_version: int
    payload_model: type[BaseModel]
    handler: PlatformJobHandler
    policy: PlatformJobPolicy
    encrypt_payload: bool = False
    # Product-surface copy for the Isolated Jobs list in Settings -> Kubernetes.
    # Only definitions with execution_class="build" are listed; others may
    # leave these unset.
    display_name: str | None = None
    description: str | None = None


def define_platform_job(
    job_type: str,
    payload_model: type[BaseModel],
    handler: PlatformJobHandler,
) -> PlatformJobDefinition:
    """Materialize a handler definition from the immutable metadata catalog."""
    metadata = get_platform_job_metadata(job_type)
    if metadata is None:
        raise ValueError(f"No platform-job metadata is registered for {job_type}")
    return PlatformJobDefinition(
        job_type=metadata.job_type,
        payload_version=metadata.payload_version,
        payload_model=payload_model,
        handler=handler,
        policy=metadata.policy,
        encrypt_payload=metadata.encrypt_payload,
        display_name=metadata.display_name,
        description=metadata.description,
    )
