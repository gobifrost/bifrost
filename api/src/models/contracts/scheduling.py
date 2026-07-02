"""
Async execution and scheduling contract models for Bifrost.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ==================== ASYNC EXECUTION ====================


class AsyncExecutionStatus(str, Enum):
    """Async execution status values"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AsyncExecution(BaseModel):
    """Async workflow execution tracking"""
    execution_id: str = Field(default_factory=lambda: str(__import__('uuid').uuid4()))
    workflow_id: str = Field(..., description="Workflow name to execute")
    status: AsyncExecutionStatus = Field(default=AsyncExecutionStatus.QUEUED)
    parameters: dict[str, Any] = Field(default_factory=dict, description="Workflow input parameters")
    context: dict[str, Any] = Field(default_factory=dict, description="Execution context (org scope, user)")
    result: Any | None = Field(default=None, description="Workflow result (for small results)")
    result_blob_uri: str | None = Field(default=None, description="Blob URI for large results (>32KB)")
    error: str | None = Field(default=None, description="Error message if failed")
    error_details: dict[str, Any] | None = Field(default=None, description="Detailed error information")
    queued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, description="Execution duration in milliseconds")


# ==================== CRON VALIDATION ====================


class CronValidationRequest(BaseModel):
    """Request model for CRON validation"""
    expression: str = Field(..., description="CRON expression to validate")
    timezone: str = Field(
        default="UTC",
        description="Timezone used to evaluate the CRON expression",
    )


class CronValidationResponse(BaseModel):
    """Response model for CRON validation"""
    valid: bool = Field(..., description="Whether the CRON expression is valid")
    human_readable: str = Field(..., description="Human-readable description")
    next_runs: list[str] | None = Field(default=None, description="Next 5 execution times (ISO format)")
    interval_seconds: int | None = Field(default=None, description="Seconds between executions")
    warning: str | None = Field(default=None, description="Warning message for too-frequent schedules")
    error: str | None = Field(default=None, description="Error message for invalid expressions")
