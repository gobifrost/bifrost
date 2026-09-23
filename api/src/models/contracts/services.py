"""
Service lifecycle contract models for Bifrost.

Services are long-lived supervised executables declared with @service
(type='service' on the workflows table). These DTOs cover the service control
plane: definitions, policy, desired state, and attempt history.

No MCP surface in Slice 2 (REST + UI only); DTO_EXCLUDES is not needed because
these DTOs are not in dto_flags COVERED_DTOS. The services CLI arrives later.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

StartupPolicy = Literal["automatic", "manual"]
RestartPolicy = Literal["always", "on_failure", "never"]
DesiredState = Literal["running", "stopped"]
BlockedReason = Literal["policy", "crash_loop", "disabled"]


class ServicePolicyUpdate(BaseModel):
    """Patch service lifecycle policy. All fields optional."""

    startup_policy: StartupPolicy | None = Field(default=None, description="automatic | manual")
    restart_policy: RestartPolicy | None = Field(default=None, description="always | on_failure | never")
    graceful_shutdown_seconds: int | None = Field(default=None, ge=0, le=3600, description="SIGTERM grace before SIGKILL")
    startup_grace_seconds: int | None = Field(default=None, ge=1, le=3600, description="Max time to report ready")
    restart_backoff_initial_seconds: int | None = Field(default=None, ge=0, le=300, description="First restart delay")
    restart_backoff_max_seconds: int | None = Field(default=None, ge=0, le=3600, description="Backoff cap (0 = no delay)")
    crash_loop_max_restarts: int | None = Field(default=None, ge=1, le=100, description="Restarts before crash-loop")
    crash_loop_window_seconds: int | None = Field(default=None, ge=60, le=86400, description="Rolling window for crash accounting")


class ServiceAttemptResponse(BaseModel):
    """One supervised run of a service."""

    id: UUID = Field(description="Attempt UUID")
    service_id: UUID = Field(description="Parent definition UUID")
    revision: str | None = Field(default=None, description="Source revision launched")
    worker_id: str | None = Field(default=None, description="Owning worker (informational; fencing is by lease token)")
    state: str = Field(description="starting | running | stopping | stopped | failed")
    ready_at: datetime | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    heartbeat_at: datetime | None = Field(default=None)
    stop_requested_at: datetime | None = Field(default=None)
    stopped_at: datetime | None = Field(default=None)
    exit_code: int | None = Field(default=None)
    exit_reason: str | None = Field(default=None)
    error: str | None = Field(default=None)
    restart_number: int = Field(description="Attempts claimed before this one")
    created_at: datetime = Field(description="Claim timestamp")

    model_config = {"from_attributes": True}


class ServiceResponse(BaseModel):
    """A service definition with its source identity and observed state."""

    id: UUID = Field(description="Service definition UUID")
    workflow_id: UUID = Field(description="Source workflow row UUID")
    workflow_name: str = Field(description="Source workflow display name")
    workflow_path: str = Field(description="Source file path")
    organization_id: UUID | None = Field(default=None, description="Org scope (null = global)")
    solution_id: UUID | None = Field(default=None, description="Owning Solution install (null = ad-hoc)")
    enabled: bool = Field(description="Operator switch; disabled services never run")
    startup_policy: StartupPolicy = Field(description="automatic | manual")
    restart_policy: RestartPolicy = Field(description="always | on_failure | never")
    desired_state: DesiredState = Field(description="running | stopped")
    blocked_reason: BlockedReason | None = Field(default=None, description="Launch suppression, if any")
    restart_eligible_at: datetime | None = Field(default=None, description="Earliest next attempt")
    current_revision: str | None = Field(default=None, description="Pinned source revision")
    graceful_shutdown_seconds: int = Field(description="SIGTERM grace before SIGKILL")
    startup_grace_seconds: int = Field(description="Max time to report ready")
    restart_backoff_initial_seconds: int = Field(description="First restart delay")
    restart_backoff_max_seconds: int = Field(description="Backoff cap")
    crash_loop_max_restarts: int = Field(description="Restarts before crash-loop")
    crash_loop_window_seconds: int = Field(description="Rolling window for crash accounting")
    # Observed state (derived, not stored on the definition).
    observed_state: str = Field(description="stopped | starting | running | stopping | restarting | crash_loop")
    active_attempt_id: UUID | None = Field(default=None, description="Live attempt, if any")
    active_attempt: ServiceAttemptResponse | None = Field(default=None, description="Live attempt summary, if any")
    last_exit_reason: str | None = Field(default=None, description="Newest terminal attempt's exit reason, if any")
    memory_mb: float | None = Field(default=None, description="Live child memory from the owning worker's pool hash (<90s old), if any")
    restart_count: int = Field(default=0, description="Total attempts ever claimed")
    created_by: str = Field(description="Who created the definition")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")

    model_config = {"from_attributes": True}


class ServiceListResponse(BaseModel):
    """Paginated service definitions."""

    items: list[ServiceResponse] = Field(description="Service definitions")
    total: int = Field(description="Total matching definitions")


class ServiceAttemptListResponse(BaseModel):
    """Paginated service attempts, newest first."""

    items: list[ServiceAttemptResponse] = Field(description="Attempts")
    total: int = Field(description="Total attempts for this service")


class ServiceLogResponse(BaseModel):
    """One persisted service log line."""

    id: int = Field(description="Row id (chronological within a service)")
    service_id: UUID = Field(description="Parent definition UUID")
    attempt_id: UUID = Field(description="Attempt that emitted the line")
    level: str = Field(description="INFO | WARNING | ERROR | DEBUG | ...")
    message: str = Field(description="Log message text")
    timestamp: datetime = Field(description="Emission timestamp")

    model_config = {"from_attributes": True}


class ServiceLogListResponse(BaseModel):
    """Trailing service logs in the requested order (with total)."""

    items: list[ServiceLogResponse] = Field(description="Log lines")
    total: int = Field(description="Total lines matching the filters")
    continuation_token: str | None = Field(
        default=None,
        description="Keyset cursor for the next page (same encoding as execution logs), if any",
    )
