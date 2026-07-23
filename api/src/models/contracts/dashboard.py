"""
Dashboard and metrics contract models for Bifrost.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


# ==================== DASHBOARD MODELS ====================


class ExecutionStats(BaseModel):
    """Execution statistics for dashboard"""
    total_executions: int
    success_count: int
    failed_count: int
    running_count: int
    pending_count: int
    success_rate: float
    avg_duration_seconds: float


class RecentFailure(BaseModel):
    """Recent failed execution info"""
    execution_id: str
    workflow_name: str
    error_message: str | None
    started_at: str | None


class ROISnapshot(BaseModel):
    """ROI metrics snapshot."""
    total_time_saved: int = Field(description="Total time saved in minutes (24h)")
    total_value: float = Field(description="Total value generated (24h)")
    time_saved_unit: str = Field(description="Display label for time saved")
    value_unit: str = Field(description="Display label for value")


class DashboardMetricsResponse(BaseModel):
    """Dashboard metrics response"""
    workflow_count: int
    data_provider_count: int
    form_count: int
    execution_stats: ExecutionStats
    recent_failures: list[RecentFailure]
    roi_24h: ROISnapshot | None = None


class ExecutionTimeSeriesBucket(BaseModel):
    """One zero-filled execution chart bucket."""

    start: datetime
    success_count: int
    failed_count: int


class ExecutionTimeSeriesResponse(BaseModel):
    """Volume-independent execution outcomes for a dashboard chart window."""

    window: Literal["24h", "7d", "30d"]
    timezone: str
    buckets: list[ExecutionTimeSeriesBucket]
    success_count: int
    failed_count: int
    total_count: int
    success_rate: float | None


class PlatformMetricsResponse(BaseModel):
    """
    Platform metrics snapshot response.

    Pre-computed metrics for instant dashboard loads.
    Refreshed by scheduler every 1-5 minutes.
    """
    # Entity counts
    workflow_count: int
    form_count: int
    data_provider_count: int
    organization_count: int
    user_count: int
    # Execution stats (all time)
    total_executions: int
    total_success: int
    total_failed: int
    # Execution stats (last 24 hours)
    executions_24h: int
    success_24h: int
    failed_24h: int
    # Current state
    running_count: int
    pending_count: int
    # Performance (last 24 hours)
    avg_duration_ms_24h: int
    total_memory_bytes_24h: int
    total_cpu_seconds_24h: float
    # Success rates
    success_rate_all_time: float
    success_rate_24h: float
    # Timestamp
    refreshed_at: str


class DailyMetricsEntry(BaseModel):
    """Single day's execution metrics."""
    date: str
    organization_id: str | None = None
    organization_name: str | None = None
    # Counts
    execution_count: int
    success_count: int
    failed_count: int
    timeout_count: int
    cancelled_count: int
    # Duration
    avg_duration_ms: int
    max_duration_ms: int
    # Resources
    peak_memory_bytes: int
    total_memory_bytes: int
    peak_cpu_seconds: float
    total_cpu_seconds: float


class DailyMetricsResponse(BaseModel):
    """Response for daily execution metrics trends."""
    days: list[DailyMetricsEntry]
    total_days: int


class OrganizationMetricsSummary(BaseModel):
    """Summary metrics for a single organization."""
    organization_id: str
    organization_name: str
    # Counts
    total_executions: int
    success_count: int
    failed_count: int
    success_rate: float
    # Resources
    total_memory_bytes: int
    total_cpu_seconds: float
    avg_duration_ms: int


class OrganizationMetricsResponse(BaseModel):
    """Response for organization metrics breakdown."""
    organizations: list[OrganizationMetricsSummary]
    total_organizations: int


class ResourceMetricsEntry(BaseModel):
    """Resource usage metrics for a time period."""
    date: str
    # Memory
    peak_memory_bytes: int
    total_memory_bytes: int
    avg_memory_bytes: int
    # CPU
    peak_cpu_seconds: float
    total_cpu_seconds: float
    avg_cpu_seconds: float
    # Execution count for context
    execution_count: int


class ResourceMetricsResponse(BaseModel):
    """Response for resource usage trends."""
    days: list[ResourceMetricsEntry]
    total_days: int


class WorkflowMetricsSummary(BaseModel):
    """Aggregated metrics for a single workflow."""
    workflow_name: str
    total_executions: int
    success_count: int
    failed_count: int
    success_rate: float
    avg_memory_bytes: int
    avg_duration_ms: int
    avg_cpu_seconds: float
    peak_memory_bytes: int
    max_duration_ms: int


class WorkflowMetricsResponse(BaseModel):
    """Response for workflow metrics aggregations."""
    workflows: list[WorkflowMetricsSummary]
    total_workflows: int
    sort_by: str
    days: int
