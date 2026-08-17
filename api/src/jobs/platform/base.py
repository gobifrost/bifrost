"""Handler contracts for scheduler-owned platform jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import UUID

from pydantic import BaseModel


@dataclass(frozen=True)
class PlatformJobPolicy:
    timeout_seconds: int
    max_attempts: int = 2
    max_concurrency: int | None = None
    retry_on_runner_loss: bool = True
    min_memory_headroom_mb: int = 256
    admission_memory_ratio: float = 0.85
    hard_memory_ratio: float = 0.95
    allow_running_cancellation: bool = False


class PlatformJobFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class PlatformJobCancelled(Exception):
    pass


class PlatformJobDeferred(Exception):
    """Release the runner while external child work completes the job."""

    def __init__(
        self,
        phase: str,
        result: dict[str, Any] | None = None,
        *,
        external_provider: str | None = None,
        external_run_id: str | None = None,
        external_started_at: datetime | None = None,
    ) -> None:
        super().__init__(phase)
        self.phase = phase
        self.result = result
        self.external_provider = external_provider
        self.external_run_id = external_run_id
        self.external_started_at = external_started_at


@dataclass(frozen=True)
class PlatformJobContext:
    job_id: UUID
    lease_token: UUID
    organization_id: UUID | None
    requested_by_user_id: str
    requested_by_email: str
    requested_by_name: str

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


PlatformJobHandler = Callable[
    [PlatformJobContext, BaseModel],
    Awaitable[dict[str, Any] | None],
]
PlatformJobCancellationHandler = Callable[[UUID, BaseModel], Awaitable[None]]


@dataclass(frozen=True)
class PlatformJobDefinition:
    job_type: str
    payload_version: int
    payload_model: type[BaseModel]
    handler: PlatformJobHandler
    policy: PlatformJobPolicy
    encrypt_payload: bool = False
    cancellation_handler: PlatformJobCancellationHandler | None = None
