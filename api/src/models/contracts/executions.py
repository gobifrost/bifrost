"""
Execution contract models for Bifrost.
"""

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from src.models.enums import ExecutionModel, ExecutionStatus

if TYPE_CHECKING:
    pass


# ==================== WORKFLOW EXECUTION MODELS ====================


class ExecutionLogPublic(BaseModel):
    """Single log entry from workflow execution (API response model)"""
    id: int  # Unique log ID for exact deduplication
    timestamp: str
    level: str  # debug, info, warning, error
    message: str
    data: dict[str, Any] | None = None
    sequence: int  # For ordering and range-based deduplication


class LogListEntry(BaseModel):
    """Single log entry for the logs list view."""
    id: int
    execution_id: str
    organization_name: str | None
    workflow_name: str
    level: str
    message: str
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class LogsListResponse(BaseModel):
    """Response for paginated logs list."""
    logs: list[LogListEntry]
    continuation_token: str | None = None


class AIUsagePublicSimple(BaseModel):
    """Simplified AI usage for embedding in execution responses."""
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: str | None = None  # Decimal as string
    duration_ms: int | None = None
    timestamp: str  # ISO datetime string
    sequence: int = 1


class AIUsageTotalsSimple(BaseModel):
    """Aggregated AI usage totals for embedding in execution responses."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: str = "0"  # Decimal as string
    total_duration_ms: int = 0
    call_count: int = 0


class WorkflowExecution(BaseModel):
    """Workflow execution entity"""
    execution_id: str
    workflow_name: str
    workflow_id: str | None = None  # FK to workflows table (null for inline scripts/legacy)
    org_id: str | None = None  # Organization ID for display/filtering
    org_name: str | None = None  # Organization name for display (effective scope)
    form_id: str | None = None
    executed_by: str
    executed_by_name: str  # Display name of user who executed
    executed_by_email: str | None = None  # Email of user who executed
    status: ExecutionStatus
    input_data: dict[str, Any]
    result: dict[str, Any] | list[Any] | str | None = Field(default=None)  # Can be dict/list (JSON) or str (HTML/text)
    result_type: str | None = None  # How to render result (json, html, text)
    error_message: str | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None  # May be None if not started yet
    completed_at: datetime | None = None
    scheduled_at: datetime | None = None  # For Scheduled rows, when the row is due to promote
    logs: list[dict[str, Any]] | None = None  # Structured logger output (replaces old ExecutionLog format)
    variables: dict[str, Any] | None = None  # Runtime variables captured from execution scope
    # CLI session tracking
    session_id: str | None = None  # CLI session ID if executed from local runner
    # Resource metrics (admin only, null for non-admins)
    peak_memory_bytes: int | None = None
    process_rss_bytes: int | None = None
    cpu_total_seconds: float | None = None
    # Execution model tracking
    execution_model: str | None = None  # 'process' or 'thread' - which worker model ran this
    # ROI economics (admin only)
    time_saved: int = 0  # Minutes saved
    value: float = 0  # Value generated
    # AI usage tracking
    ai_usage: list[AIUsagePublicSimple] | None = None
    ai_totals: AIUsageTotalsSimple | None = None
    # Persisted execution context (admin only)
    execution_context: dict[str, Any] | None = None


class WorkflowExecutionRequest(BaseModel):
    """Request model for executing a workflow"""
    workflow_id: str | None = Field(default=None, description="Workflow UUID or name. Names resolve using org-scoped lookup (org-specific > global). Required if code not provided.")
    input_data: dict[str, Any] = Field(default_factory=dict, description="Workflow input parameters")
    form_id: str | None = Field(default=None, description="Optional form ID that triggered this execution")
    app_id: str | None = Field(default=None, description="Optional app ID of the calling Solution app. Used to scope a path::function workflow ref to the app's own install (so it resolves the install's own workflow, not a sibling install's that shares the path).")
    solution_id: str | None = Field(default=None, description="Optional install id of the calling Solution form/agent. Used to scope a path::function workflow ref to that install (so it resolves the install's own workflow, not a sibling install's or the bare _repo/ one). Takes precedence over form_id/app_id derivation.")
    transient: bool = Field(default=False, description="If true, skip database persistence (for code editor debugging)")
    sync: bool | None = Field(default=None, description="If true, block until execution completes and return result inline. Overrides workflow's execution_mode setting.")
    code: str | None = Field(default=None, description="Optional: Python code to execute as script (base64 encoded). If provided, executes code instead of looking up workflow by ID.")
    script_name: str | None = Field(default=None, description="Optional: Name/identifier for the script (used for logging when code is provided)")
    org_id: str | None = Field(default=None, description="Override execution org context. Requires platform admin.")
    run_as: str | None = Field(default=None, description="Execute as this user UUID (impersonation). Requires platform admin.")
    scheduled_at: datetime | None = Field(
        default=None,
        description=(
            "Run at this tz-aware timestamp (ISO-8601). Must be strictly in the "
            "future and within 1 year of now. Mutually exclusive with delay_seconds."
        ),
    )
    delay_seconds: int | None = Field(
        default=None,
        ge=1,
        le=31_536_000,
        description=(
            "Run this many seconds from now (≤ 1 year). "
            "Mutually exclusive with scheduled_at."
        ),
    )

    @model_validator(mode="after")
    def validate_request(self) -> "WorkflowExecutionRequest":
        if not self.workflow_id and not self.code:
            raise ValueError("Either 'workflow_id' or 'code' must be provided")

        if self.scheduled_at is not None and self.delay_seconds is not None:
            raise ValueError(
                "'scheduled_at' and 'delay_seconds' are mutually exclusive"
            )

        if self.scheduled_at is not None:
            if self.scheduled_at.tzinfo is None:
                raise ValueError("'scheduled_at' must be timezone-aware")
            now = datetime.now(timezone.utc)
            if self.scheduled_at <= now:
                raise ValueError("'scheduled_at' must be in the future")
            if self.scheduled_at > now + timedelta(days=365):
                raise ValueError("'scheduled_at' must be within 1 year")

        if (self.scheduled_at is not None or self.delay_seconds is not None):
            if self.sync:
                raise ValueError("'sync' cannot be combined with scheduling")
            if self.code:
                raise ValueError("'code' (inline) cannot be scheduled")

        return self


class WorkflowExecutionResponse(BaseModel):
    """Response model for workflow execution"""
    execution_id: str
    workflow_id: str | None = None
    workflow_name: str | None = None  # Display name from @workflow decorator
    status: ExecutionStatus
    result: dict[str, Any] | list[Any] | str | None = Field(default=None)  # Can be dict/list (JSON) or str (HTML/text)
    error: str | None = None
    error_type: str | None = None
    details: dict[str, Any] | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    scheduled_at: datetime | None = Field(
        default=None,
        description="For scheduled executions, the target run time.",
    )
    # Enhanced debugging output for code editor
    logs: list[dict[str, Any]] | None = None  # Structured logger output
    variables: dict[str, Any] | None = None  # Runtime variables from execution scope
    is_transient: bool = False  # Flag for editor executions (no DB persistence)


class ExecutionsListResponse(BaseModel):
    """Response model for listing workflow executions with pagination"""
    executions: list[WorkflowExecution] = Field(..., description="List of workflow executions")
    continuation_token: str | None = Field(default=None, description="Continuation token for next page (opaque, base64-encoded). Presence of token indicates more results available.")


class StuckExecutionsResponse(BaseModel):
    """Response model for stuck executions query"""
    executions: list[WorkflowExecution] = Field(..., description="List of stuck executions")
    count: int = Field(..., description="Number of stuck executions found")


class CleanupTriggeredResponse(BaseModel):
    """Response model for cleanup trigger operation"""
    cleaned: int = Field(..., description="Total number of executions cleaned up")
    pending: int = Field(..., description="Number of pending executions timed out")
    running: int = Field(..., description="Number of running executions timed out")
    failed: int = Field(..., description="Number of executions that failed to clean up")


# CRUD Pattern Models for Execution
class ExecutionBase(BaseModel):
    """Shared execution fields."""
    workflow_name: str = Field(max_length=255)
    workflow_version: str | None = Field(default=None, max_length=50)
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)
    parameters: dict = Field(default_factory=dict)
    result: dict | None = Field(default=None)
    result_type: str | None = Field(default=None, max_length=50)
    variables: dict | None = Field(default=None)
    error_message: str | None = Field(default=None)


class ExecutionCreate(BaseModel):
    """Input for creating an execution."""
    workflow_name: str
    workflow_version: str | None = None
    parameters: dict = Field(default_factory=dict)
    form_id: UUID | None = None


class ExecutionUpdate(BaseModel):
    """Input for updating an execution (typically status updates)."""
    status: ExecutionStatus | None = None
    result: dict | None = None
    result_type: str | None = None
    variables: dict | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    execution_model: ExecutionModel | None = Field(
        default=None,
        description="Execution model used: 'process' (legacy) or 'thread' (new thread pool)"
    )


class ExecutionPublic(ExecutionBase):
    """Execution output for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    executed_by: UUID
    executed_by_name: str
    organization_id: UUID | None
    form_id: UUID | None
    created_at: datetime
    execution_model: str | None = Field(
        default=None,
        description="Execution model used: 'process' (legacy) or 'thread' (new thread pool)"
    )

    @field_serializer("created_at", "started_at", "completed_at")
    def serialize_dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None


