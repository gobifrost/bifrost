"""Public contracts for durable scheduler-owned platform jobs."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlatformJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlatformJobProgress(BaseModel):
    phase: str | None = None
    current: int = 0
    total: int | None = None
    percent: float | None = Field(default=None, ge=0, le=100)


class PlatformJobError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class PlatformJobAccepted(BaseModel):
    """Response returned immediately after a platform job is enqueued."""

    job_id: UUID
    status: PlatformJobStatus
    reused: bool = False
    notification_id: UUID | None = None


class PlatformJobPublic(BaseModel):
    """Sanitized durable state returned by HTTP and WebSocket."""

    id: UUID
    job_type: str
    payload_version: int
    organization_id: UUID | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    title: str
    action_url: str | None = None
    requested_by_user_id: str
    requested_by_name: str
    status: PlatformJobStatus
    progress: PlatformJobProgress
    revision: int
    attempt: int
    max_attempts: int
    can_cancel: bool
    result: dict[str, Any] | None = None
    error: PlatformJobError | None = None
    notification_id: UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlatformJobListResponse(BaseModel):
    jobs: list[PlatformJobPublic]


class PlatformJobCancelResponse(BaseModel):
    job: PlatformJobPublic
    accepted: bool
