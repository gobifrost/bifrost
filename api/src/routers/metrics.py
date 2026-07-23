"""
Metrics Router

Dashboard metrics and statistics.
Provides overview of platform usage and system health.

Endpoint Structure:
- GET /api/metrics - Main dashboard metrics (uses pre-computed snapshot for instant loads)
- GET /api/metrics/executions/daily - Daily execution trends (platform admin only)
- GET /api/metrics/organizations - Organization breakdown (platform admin only)
- GET /api/metrics/resources - Resource usage trends (platform admin only)
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, func, case, desc

from src.models import (
    DashboardMetricsResponse,
    ExecutionStats,
    ExecutionTimeSeriesBucket,
    ExecutionTimeSeriesResponse,
    RecentFailure,
    ROISnapshot,
    PlatformMetricsResponse,
    DailyMetricsEntry,
    DailyMetricsResponse,
    OrganizationMetricsSummary,
    OrganizationMetricsResponse,
    ResourceMetricsEntry,
    ResourceMetricsResponse,
    WorkflowMetricsSummary,
    WorkflowMetricsResponse,
)
from src.core.auth import Context, CurrentActiveUser, RequirePlatformAdmin
from src.models import Execution as ExecutionModel
from src.models import ExecutionMetricsDaily, Organization
from src.models.orm import PlatformMetricsSnapshot as PlatformMetricsSnapshotORM
from src.models.enums import ExecutionStatus
from src.services.roi_settings_service import ROISettingsService
from shared.execution_timeseries import get_execution_time_series

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metrics", tags=["Metrics"])


# =============================================================================
# HTTP Endpoints
# =============================================================================


@router.get(
    "",
    response_model=DashboardMetricsResponse,
    summary="Get dashboard metrics",
    description="Get platform metrics for the dashboard. Uses pre-computed snapshot for instant loads.",
)
async def get_metrics(
    ctx: Context,
    user: CurrentActiveUser,
) -> DashboardMetricsResponse:
    """
    Get dashboard metrics.

    Uses the pre-computed platform_metrics_snapshot table for instant response.
    Falls back to direct queries if snapshot is not available.

    Authorization note: this is the universal landing dashboard and is
    deliberately available to any active user (CurrentActiveUser), unlike the
    admin-only detail/snapshot siblings below (RequirePlatformAdmin). The
    exposed aggregates are intentionally non-sensitive platform-wide counts.

    Returns:
        Dashboard metrics including workflow/form counts, execution stats, etc.
    """
    try:
        # Try to use pre-computed snapshot first (instant)
        snapshot_query = select(PlatformMetricsSnapshotORM).where(
            PlatformMetricsSnapshotORM.id == 1
        )
        snapshot_result = await ctx.db.execute(snapshot_query)
        snapshot = snapshot_result.scalar_one_or_none()

        if snapshot:
            # Use snapshot data
            execution_stats = ExecutionStats(
                total_executions=snapshot.total_executions,
                success_count=snapshot.total_success,
                failed_count=snapshot.total_failed,
                running_count=snapshot.running_count,
                pending_count=snapshot.pending_count,
                success_rate=snapshot.success_rate_all_time,
                avg_duration_seconds=float(snapshot.avg_duration_ms_24h) / 1000.0,
            )

            # Still need to fetch recent failures (they change frequently)
            recent_failures = await _get_recent_failures(ctx)

            # Fetch ROI settings for display units
            roi_service = ROISettingsService(ctx.db)
            roi_settings = await roi_service.get_settings()

            # Build ROI snapshot
            roi_24h = ROISnapshot(
                total_time_saved=snapshot.time_saved_24h,
                total_value=float(snapshot.value_24h),
                time_saved_unit=roi_settings.time_saved_unit,
                value_unit=roi_settings.value_unit,
            )

            response = DashboardMetricsResponse(
                workflow_count=snapshot.workflow_count,
                form_count=snapshot.form_count,
                data_provider_count=snapshot.data_provider_count,
                execution_stats=execution_stats,
                recent_failures=recent_failures,
                roi_24h=roi_24h,
            )

            logger.debug(
                f"Dashboard metrics from snapshot (refreshed {snapshot.refreshed_at})"
            )
            return response

        # Fallback: compute metrics directly (slower, for when snapshot doesn't exist)
        logger.warning("Snapshot not available, computing metrics directly")
        return await _compute_metrics_directly(ctx)

    except Exception as e:
        logger.error(f"Error getting metrics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get metrics",
        )


@router.get(
    "/snapshot",
    response_model=PlatformMetricsResponse,
    summary="Get full platform metrics snapshot",
    description="Get the complete pre-computed metrics snapshot. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_metrics_snapshot(
    ctx: Context,
    user: CurrentActiveUser,
) -> PlatformMetricsResponse:
    """
    Get the full platform metrics snapshot.

    Returns all pre-computed metrics including organization counts,
    24h stats, resource usage, etc.

    Platform admin only.
    """
    try:
        snapshot_query = select(PlatformMetricsSnapshotORM).where(
            PlatformMetricsSnapshotORM.id == 1
        )
        snapshot_result = await ctx.db.execute(snapshot_query)
        snapshot = snapshot_result.scalar_one_or_none()

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Metrics snapshot not available",
            )

        return PlatformMetricsResponse(
            workflow_count=snapshot.workflow_count,
            form_count=snapshot.form_count,
            data_provider_count=snapshot.data_provider_count,
            organization_count=snapshot.organization_count,
            user_count=snapshot.user_count,
            total_executions=snapshot.total_executions,
            total_success=snapshot.total_success,
            total_failed=snapshot.total_failed,
            executions_24h=snapshot.executions_24h,
            success_24h=snapshot.success_24h,
            failed_24h=snapshot.failed_24h,
            running_count=snapshot.running_count,
            pending_count=snapshot.pending_count,
            avg_duration_ms_24h=snapshot.avg_duration_ms_24h,
            total_memory_bytes_24h=snapshot.total_memory_bytes_24h,
            total_cpu_seconds_24h=snapshot.total_cpu_seconds_24h,
            success_rate_all_time=snapshot.success_rate_all_time,
            success_rate_24h=snapshot.success_rate_24h,
            refreshed_at=snapshot.refreshed_at.isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting metrics snapshot: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get metrics snapshot",
        )


@router.get(
    "/executions/timeseries",
    response_model=ExecutionTimeSeriesResponse,
    summary="Get dashboard execution time series",
    description=(
        "Get zero-filled hourly or daily execution outcomes for a dashboard "
        "chart window."
    ),
)
async def get_dashboard_execution_time_series(
    ctx: Context,
    window: Literal["24h", "7d", "30d"] = Query(
        default="7d",
        description="Dashboard chart window",
    ),
    timezone_name: str = Query(
        default="UTC",
        alias="timezone",
        min_length=1,
        max_length=64,
        description="IANA timezone used for hour and day boundaries",
    ),
    scope: str | None = Query(
        default=None,
        description=(
            "Filter scope: omit for all (superusers), 'global' for global only, "
            "or an organization UUID."
        ),
    ),
) -> ExecutionTimeSeriesResponse:
    """Return the dashboard chart aggregate with execution-list visibility."""

    try:
        series = await get_execution_time_series(
            ctx.db,
            ctx.user,
            window,
            timezone_name,
            scope,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ExecutionTimeSeriesResponse(
        window=series.window,
        timezone=series.timezone,
        buckets=[
            ExecutionTimeSeriesBucket(
                start=bucket.start,
                success_count=bucket.success_count,
                failed_count=bucket.failed_count,
            )
            for bucket in series.buckets
        ],
        success_count=series.success_count,
        failed_count=series.failed_count,
        total_count=series.total_count,
        success_rate=series.success_rate,
    )


@router.get(
    "/executions/daily",
    response_model=DailyMetricsResponse,
    summary="Get daily execution metrics",
    description="Get daily execution metrics for trends. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_daily_metrics(
    ctx: Context,
    user: CurrentActiveUser,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to include"),
    organization_id: str | None = Query(default=None, description="Filter by organization"),
) -> DailyMetricsResponse:
    """
    Get daily execution metrics for trends and charts.

    Platform admin only.

    Args:
        days: Number of days to include (default 30, max 365)
        organization_id: Optional filter by organization

    Returns:
        Daily metrics entries for the requested period
    """
    try:
        start_date = date.today() - timedelta(days=days)

        query = (
            select(ExecutionMetricsDaily)
            .where(ExecutionMetricsDaily.date >= start_date)
            .order_by(ExecutionMetricsDaily.date.desc())
        )

        if organization_id:
            # Parse org ID
            from uuid import UUID
            org_uuid = UUID(organization_id.replace("ORG:", ""))
            query = query.where(ExecutionMetricsDaily.organization_id == org_uuid)
        else:
            # Get global metrics only (org_id is NULL)
            query = query.where(ExecutionMetricsDaily.organization_id.is_(None))

        result = await ctx.db.execute(query)
        rows = result.scalars().all()

        entries = [
            DailyMetricsEntry(
                date=row.date.isoformat(),
                organization_id=f"ORG:{row.organization_id}" if row.organization_id else None,
                execution_count=row.execution_count,
                success_count=row.success_count,
                failed_count=row.failed_count,
                timeout_count=row.timeout_count,
                cancelled_count=row.cancelled_count,
                avg_duration_ms=row.avg_duration_ms,
                max_duration_ms=row.max_duration_ms,
                peak_memory_bytes=row.peak_memory_bytes,
                total_memory_bytes=row.total_memory_bytes,
                peak_cpu_seconds=row.peak_cpu_seconds,
                total_cpu_seconds=row.total_cpu_seconds,
            )
            for row in rows
        ]

        return DailyMetricsResponse(
            days=entries,
            total_days=len(entries),
        )

    except Exception as e:
        logger.error(f"Error getting daily metrics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get daily metrics",
        )


@router.get(
    "/organizations",
    response_model=OrganizationMetricsResponse,
    summary="Get organization metrics breakdown",
    description="Get execution metrics grouped by organization. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_organization_metrics(
    ctx: Context,
    user: CurrentActiveUser,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to include"),
    limit: int = Query(default=20, ge=1, le=100, description="Max organizations to return"),
) -> OrganizationMetricsResponse:
    """
    Get execution metrics breakdown by organization.

    Platform admin only.

    Args:
        days: Number of days to aggregate (default 30)
        limit: Max organizations to return (default 20)

    Returns:
        Organization metrics sorted by total executions (descending)
    """
    try:
        start_date = date.today() - timedelta(days=days)

        # Aggregate metrics per organization
        query = (
            select(
                ExecutionMetricsDaily.organization_id,
                Organization.name.label("org_name"),
                func.sum(ExecutionMetricsDaily.execution_count).label("total_executions"),
                func.sum(ExecutionMetricsDaily.success_count).label("success_count"),
                func.sum(ExecutionMetricsDaily.failed_count).label("failed_count"),
                func.sum(ExecutionMetricsDaily.total_memory_bytes).label("total_memory"),
                func.sum(ExecutionMetricsDaily.total_cpu_seconds).label("total_cpu"),
                func.sum(ExecutionMetricsDaily.total_duration_ms).label("total_duration"),
            )
            .join(Organization, ExecutionMetricsDaily.organization_id == Organization.id)
            .where(ExecutionMetricsDaily.date >= start_date)
            .where(ExecutionMetricsDaily.organization_id.isnot(None))
            .group_by(ExecutionMetricsDaily.organization_id, Organization.name)
            .order_by(func.sum(ExecutionMetricsDaily.execution_count).desc())
            .limit(limit)
        )

        result = await ctx.db.execute(query)
        rows = result.all()

        organizations = []
        for row in rows:
            total = row.total_executions or 0
            success = row.success_count or 0
            success_rate = (success / total * 100) if total > 0 else 0.0
            avg_duration = int(row.total_duration / total) if total > 0 else 0

            organizations.append(
                OrganizationMetricsSummary(
                    organization_id=f"ORG:{row.organization_id}",
                    organization_name=row.org_name or "Unknown",
                    total_executions=total,
                    success_count=success,
                    failed_count=row.failed_count or 0,
                    success_rate=round(success_rate, 2),
                    total_memory_bytes=row.total_memory or 0,
                    total_cpu_seconds=float(row.total_cpu or 0),
                    avg_duration_ms=avg_duration,
                )
            )

        return OrganizationMetricsResponse(
            organizations=organizations,
            total_organizations=len(organizations),
        )

    except Exception as e:
        logger.error(f"Error getting organization metrics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get organization metrics",
        )


@router.get(
    "/resources",
    response_model=ResourceMetricsResponse,
    summary="Get resource usage trends",
    description="Get memory and CPU usage trends. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_resource_metrics(
    ctx: Context,
    user: CurrentActiveUser,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to include"),
) -> ResourceMetricsResponse:
    """
    Get resource usage trends (memory, CPU).

    Platform admin only.

    Args:
        days: Number of days to include (default 30)

    Returns:
        Daily resource metrics for the requested period
    """
    try:
        start_date = date.today() - timedelta(days=days)

        # Get global metrics (org_id is NULL)
        query = (
            select(ExecutionMetricsDaily)
            .where(ExecutionMetricsDaily.date >= start_date)
            .where(ExecutionMetricsDaily.organization_id.is_(None))
            .order_by(ExecutionMetricsDaily.date.desc())
        )

        result = await ctx.db.execute(query)
        rows = result.scalars().all()

        entries = []
        for row in rows:
            exec_count = row.execution_count or 1  # Avoid division by zero
            entries.append(
                ResourceMetricsEntry(
                    date=row.date.isoformat(),
                    peak_memory_bytes=row.peak_memory_bytes,
                    total_memory_bytes=row.total_memory_bytes,
                    avg_memory_bytes=int(row.total_memory_bytes / exec_count),
                    peak_cpu_seconds=row.peak_cpu_seconds,
                    total_cpu_seconds=row.total_cpu_seconds,
                    avg_cpu_seconds=round(row.total_cpu_seconds / exec_count, 3),
                    execution_count=row.execution_count,
                )
            )

        return ResourceMetricsResponse(
            days=entries,
            total_days=len(entries),
        )

    except Exception as e:
        logger.error(f"Error getting resource metrics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get resource metrics",
        )


@router.get(
    "/workflows",
    response_model=WorkflowMetricsResponse,
    summary="Get workflow-level metrics",
    description="Get aggregated execution metrics grouped by workflow. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_workflow_metrics(
    ctx: Context,
    user: CurrentActiveUser,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to include"),
    sort_by: str = Query(
        default="executions",
        description="Sort by: executions, memory, duration, cpu",
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Max workflows to return"),
) -> WorkflowMetricsResponse:
    """
    Get workflow-level execution metrics.

    Platform admin only.

    Args:
        days: Number of days to aggregate (default 30)
        sort_by: Sort field (executions, memory, duration, cpu)
        limit: Max workflows to return (default 20)

    Returns:
        Workflow metrics sorted by the specified field (descending)
    """
    try:
        start_date = datetime.now(timezone.utc) - timedelta(days=days)

        # Define aggregation columns
        total_executions = func.count(ExecutionModel.id).label("total_executions")
        success_count = func.sum(
            case((ExecutionModel.status == ExecutionStatus.SUCCESS.value, 1), else_=0)
        ).label("success_count")
        failed_count = func.sum(
            case((ExecutionModel.status == ExecutionStatus.FAILED.value, 1), else_=0)
        ).label("failed_count")
        avg_memory = func.avg(ExecutionModel.peak_memory_bytes).label("avg_memory")
        avg_duration = func.avg(ExecutionModel.duration_ms).label("avg_duration")
        avg_cpu = func.avg(ExecutionModel.cpu_total_seconds).label("avg_cpu")
        peak_memory = func.max(ExecutionModel.peak_memory_bytes).label("peak_memory")
        max_duration = func.max(ExecutionModel.duration_ms).label("max_duration")

        # Map sort_by to columns
        sort_columns = {
            "executions": total_executions,
            "memory": avg_memory,
            "duration": avg_duration,
            "cpu": avg_cpu,
        }
        order_col = sort_columns.get(sort_by, total_executions)

        query = (
            select(
                ExecutionModel.workflow_name,
                total_executions,
                success_count,
                failed_count,
                avg_memory,
                avg_duration,
                avg_cpu,
                peak_memory,
                max_duration,
            )
            .where(ExecutionModel.created_at >= start_date)
            .where(ExecutionModel.workflow_name.isnot(None))
            .group_by(ExecutionModel.workflow_name)
            .order_by(desc(order_col))
            .limit(limit)
        )

        result = await ctx.db.execute(query)
        rows = result.all()

        workflows = []
        for row in rows:
            total = row.total_executions or 0
            success = row.success_count or 0
            success_rate = (success / total * 100) if total > 0 else 0.0

            workflows.append(
                WorkflowMetricsSummary(
                    workflow_name=row.workflow_name,
                    total_executions=total,
                    success_count=success,
                    failed_count=row.failed_count or 0,
                    success_rate=round(success_rate, 2),
                    avg_memory_bytes=int(row.avg_memory or 0),
                    avg_duration_ms=int(row.avg_duration or 0),
                    avg_cpu_seconds=round(float(row.avg_cpu or 0), 3),
                    peak_memory_bytes=int(row.peak_memory or 0),
                    max_duration_ms=int(row.max_duration or 0),
                )
            )

        return WorkflowMetricsResponse(
            workflows=workflows,
            total_workflows=len(workflows),
            sort_by=sort_by,
            days=days,
        )

    except Exception as e:
        logger.error(f"Error getting workflow metrics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get workflow metrics",
        )


# =============================================================================
# Helper Functions
# =============================================================================


async def _get_recent_failures(ctx: Context, limit: int = 5) -> list[RecentFailure]:
    """Get recent failed executions."""
    failures_query = (
        select(
            ExecutionModel.id,
            ExecutionModel.workflow_name,
            ExecutionModel.error_message,
            ExecutionModel.started_at,
        )
        .where(ExecutionModel.status == ExecutionStatus.FAILED.value)
        .order_by(ExecutionModel.started_at.desc())
        .limit(limit)
    )
    failures_result = await ctx.db.execute(failures_query)
    failures_rows = failures_result.all()

    return [
        RecentFailure(
            execution_id=str(f.id),
            workflow_name=f.workflow_name or "Unknown",
            error_message=f.error_message,
            started_at=f.started_at.isoformat() if f.started_at else None,
        )
        for f in failures_rows
    ]


async def _compute_metrics_directly(ctx: Context) -> DashboardMetricsResponse:
    """
    Compute metrics directly from tables.

    Fallback when snapshot is not available.
    """
    from sqlalchemy import case, literal_column
    from src.models import Workflow, Form

    # Single query for all entity counts using subqueries
    # Note: Data providers are now in the workflows table with type='data_provider'
    counts_query = select(
        select(func.count(Workflow.id))
        .where(Workflow.is_active.is_(True))
        .where(Workflow.type != "data_provider")  # Exclude data providers from workflow count
        .correlate(None)
        .scalar_subquery()
        .label("workflow_count"),
        select(func.count(Form.id))
        .where(Form.is_active.is_(True))
        .correlate(None)
        .scalar_subquery()
        .label("form_count"),
        select(func.count(Workflow.id))
        .where(Workflow.is_active.is_(True))
        .where(Workflow.type == "data_provider")  # Count only data providers
        .correlate(None)
        .scalar_subquery()
        .label("provider_count"),
    )
    counts_result = await ctx.db.execute(counts_query)
    counts_row = counts_result.one()
    workflow_count = counts_row.workflow_count or 0
    form_count = counts_row.form_count or 0
    provider_count = counts_row.provider_count or 0

    # Single query for all execution stats using conditional aggregation
    exec_stats_query = select(
        func.count(ExecutionModel.id).label("total"),
        func.sum(
            case(
                (ExecutionModel.status == ExecutionStatus.SUCCESS.value, 1),
                else_=0,
            )
        ).label("success_count"),
        func.sum(
            case(
                (ExecutionModel.status == ExecutionStatus.FAILED.value, 1),
                else_=0,
            )
        ).label("failed_count"),
        func.sum(
            case(
                (ExecutionModel.status == ExecutionStatus.RUNNING.value, 1),
                else_=0,
            )
        ).label("running_count"),
        func.sum(
            case(
                (ExecutionModel.status == ExecutionStatus.PENDING.value, 1),
                else_=0,
            )
        ).label("pending_count"),
        func.avg(
            case(
                (ExecutionModel.status == ExecutionStatus.SUCCESS.value, ExecutionModel.duration_ms),
                else_=literal_column("NULL"),
            )
        ).label("avg_duration_ms"),
    )
    exec_result = await ctx.db.execute(exec_stats_query)
    exec_row = exec_result.one()

    total_executions = exec_row.total or 0
    success_count = exec_row.success_count or 0
    failed_count = exec_row.failed_count or 0
    running_count = exec_row.running_count or 0
    pending_count = exec_row.pending_count or 0
    avg_duration_ms = exec_row.avg_duration_ms or 0

    success_rate = (success_count / total_executions * 100) if total_executions > 0 else 0.0
    avg_duration_seconds = float(avg_duration_ms) / 1000.0 if avg_duration_ms else 0.0

    # Get recent failures
    recent_failures = await _get_recent_failures(ctx)

    # Execution statistics
    execution_stats = ExecutionStats(
        total_executions=total_executions,
        success_count=success_count,
        failed_count=failed_count,
        running_count=running_count,
        pending_count=pending_count,
        success_rate=success_rate,
        avg_duration_seconds=avg_duration_seconds,
    )

    # Fetch ROI settings (fallback has no ROI data)
    roi_service = ROISettingsService(ctx.db)
    roi_settings = await roi_service.get_settings()

    # ROI snapshot (fallback: no data available)
    roi_24h = ROISnapshot(
        total_time_saved=0,
        total_value=0.0,
        time_saved_unit=roi_settings.time_saved_unit,
        value_unit=roi_settings.value_unit,
    )

    return DashboardMetricsResponse(
        workflow_count=workflow_count,
        form_count=form_count,
        data_provider_count=provider_count,
        execution_stats=execution_stats,
        recent_failures=recent_failures,
        roi_24h=roi_24h,
    )
