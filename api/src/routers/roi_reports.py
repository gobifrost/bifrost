"""
ROI Reports Router

Provides ROI reports endpoints for platform administrators.
All endpoints require superuser access and query aggregated metrics data.

Endpoint Structure:
- GET /api/reports/roi/summary - Overall ROI summary for a period
- GET /api/reports/roi/by-workflow - Workflow breakdown of ROI
- GET /api/reports/roi/by-organization - Organization breakdown of ROI
- GET /api/reports/roi/trends - Time series ROI data
"""

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, func

from src.core.db_deps import DbSession
from src.models import (
    ROISummaryResponse,
    ROIByWorkflowResponse,
    WorkflowROIEntry,
    ROIByOrganizationResponse,
    OrganizationROIEntry,
    ROITrendsResponse,
    ROITrendEntry,
)
from uuid import UUID
from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.models.orm import (
    ExecutionMetricsDaily,
    WorkflowROIDaily,
    Organization,
    Workflow,
)
from src.services.roi_settings_service import ROISettingsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports/roi", tags=["ROI Reports"])


# =============================================================================
# HTTP Endpoints
# =============================================================================


@router.get(
    "/summary",
    response_model=ROISummaryResponse,
    summary="Get ROI summary",
    description="Get overall ROI summary for a date range. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_roi_summary(
    user: CurrentActiveUser,
    db: DbSession,
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all, 'global' for global only, "
        "or org UUID for specific org."
    ),
) -> ROISummaryResponse:
    """
    Get ROI summary for a period.

    Aggregates metrics from execution_metrics_daily table.
    Platform admin only.

    Organization filtering is controlled via the scope query param:
    - If param is omitted: returns platform-wide metrics (all organizations)
    - If param is 'global': returns only global metrics (org_id IS NULL)
    - If param is UUID: returns metrics for that specific organization

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        scope: Filter scope - None (all), "global", or org UUID string

    Returns:
        ROI summary including total executions, time saved, and value
    """
    # Parse scope parameter
    org_uuid: UUID | None = None
    global_only = False
    if scope:
        if scope == "global":
            global_only = True
        else:
            try:
                org_uuid = UUID(scope)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid scope value: {scope}",
                )

    try:
        # Get ROI settings for units
        settings_service = ROISettingsService(db)
        settings = await settings_service.get_settings()

        # Build query
        query = select(
            func.sum(ExecutionMetricsDaily.execution_count).label("total_executions"),
            func.sum(ExecutionMetricsDaily.success_count).label("successful_executions"),
            func.sum(ExecutionMetricsDaily.total_time_saved).label("total_time_saved"),
            func.sum(ExecutionMetricsDaily.total_value).label("total_value"),
        ).where(
            ExecutionMetricsDaily.date >= start_date,
            ExecutionMetricsDaily.date <= end_date,
        )

        # Apply organization filter from scope param
        if global_only:
            query = query.where(ExecutionMetricsDaily.organization_id.is_(None))
        elif org_uuid:
            query = query.where(ExecutionMetricsDaily.organization_id == org_uuid)
        # If no filter, return all metrics (platform admins see everything)

        result = await db.execute(query)
        row = result.one()

        return ROISummaryResponse(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            total_executions=row.total_executions or 0,
            successful_executions=row.successful_executions or 0,
            total_time_saved=row.total_time_saved or 0,
            total_value=float(row.total_value or 0),
            time_saved_unit=settings.time_saved_unit,
            value_unit=settings.value_unit,
        )

    except Exception as e:
        logger.error(f"Error getting ROI summary: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get ROI summary",
        )


@router.get(
    "/by-workflow",
    response_model=ROIByWorkflowResponse,
    summary="Get ROI by workflow",
    description="Get workflow breakdown of ROI for a date range. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_roi_by_workflow(
    user: CurrentActiveUser,
    db: DbSession,
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    limit: int = Query(default=20, ge=1, le=100, description="Max workflows to return"),
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all, 'global' for global only, "
        "or org UUID for specific org."
    ),
) -> ROIByWorkflowResponse:
    """
    Get workflow breakdown of ROI.

    Aggregates metrics from workflow_roi_daily table.
    Platform admin only.

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        limit: Max workflows to return (default 20)
        scope: Filter scope - None (all), "global", or org UUID string

    Returns:
        Workflow ROI breakdown sorted by total value (descending)
    """
    # Parse scope parameter
    org_uuid: UUID | None = None
    global_only = False
    if scope:
        if scope == "global":
            global_only = True
        else:
            try:
                org_uuid = UUID(scope)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid scope value: {scope}",
                )

    try:
        # Get ROI settings for units
        settings_service = ROISettingsService(db)
        settings = await settings_service.get_settings()

        # Build aggregation query
        query = (
            select(
                WorkflowROIDaily.workflow_id,
                Workflow.name.label("workflow_name"),
                Workflow.time_saved.label("time_saved_per_execution"),
                Workflow.value.label("value_per_execution"),
                func.sum(WorkflowROIDaily.execution_count).label("execution_count"),
                func.sum(WorkflowROIDaily.success_count).label("success_count"),
                func.sum(WorkflowROIDaily.total_time_saved).label("total_time_saved"),
                func.sum(WorkflowROIDaily.total_value).label("total_value"),
            )
            .join(Workflow, WorkflowROIDaily.workflow_id == Workflow.id)
            .where(
                WorkflowROIDaily.date >= start_date,
                WorkflowROIDaily.date <= end_date,
            )
            .group_by(
                WorkflowROIDaily.workflow_id,
                Workflow.name,
                Workflow.time_saved,
                Workflow.value,
            )
            .order_by(func.sum(WorkflowROIDaily.total_value).desc())
            .limit(limit)
        )

        # Apply organization filter from scope param
        if global_only:
            query = query.where(WorkflowROIDaily.organization_id.is_(None))
        elif org_uuid:
            query = query.where(WorkflowROIDaily.organization_id == org_uuid)

        result = await db.execute(query)
        rows = result.all()

        workflows = [
            WorkflowROIEntry(
                workflow_id=str(row.workflow_id),
                workflow_name=row.workflow_name or "Unknown",
                execution_count=row.execution_count or 0,
                success_count=row.success_count or 0,
                time_saved_per_execution=row.time_saved_per_execution or 0,
                value_per_execution=float(row.value_per_execution or 0),
                total_time_saved=row.total_time_saved or 0,
                total_value=float(row.total_value or 0),
            )
            for row in rows
        ]

        return ROIByWorkflowResponse(
            workflows=workflows,
            total_workflows=len(workflows),
            time_saved_unit=settings.time_saved_unit,
            value_unit=settings.value_unit,
        )

    except Exception as e:
        logger.error(f"Error getting ROI by workflow: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get ROI by workflow",
        )


@router.get(
    "/by-organization",
    response_model=ROIByOrganizationResponse,
    summary="Get ROI by organization",
    description="Get organization breakdown of ROI for a date range. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_roi_by_organization(
    user: CurrentActiveUser,
    db: DbSession,
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    limit: int = Query(default=20, ge=1, le=100, description="Max organizations to return"),
) -> ROIByOrganizationResponse:
    """
    Get organization breakdown of ROI.

    Aggregates metrics from execution_metrics_daily table grouped by organization.
    Platform admin only.

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        limit: Max organizations to return (default 20)

    Returns:
        Organization ROI breakdown sorted by total value (descending)
    """
    try:
        # Get ROI settings for units
        settings_service = ROISettingsService(db)
        settings = await settings_service.get_settings()

        # Build aggregation query
        query = (
            select(
                ExecutionMetricsDaily.organization_id,
                Organization.name.label("organization_name"),
                func.sum(ExecutionMetricsDaily.execution_count).label("execution_count"),
                func.sum(ExecutionMetricsDaily.success_count).label("success_count"),
                func.sum(ExecutionMetricsDaily.total_time_saved).label("total_time_saved"),
                func.sum(ExecutionMetricsDaily.total_value).label("total_value"),
            )
            .join(Organization, ExecutionMetricsDaily.organization_id == Organization.id)
            .where(
                ExecutionMetricsDaily.date >= start_date,
                ExecutionMetricsDaily.date <= end_date,
                ExecutionMetricsDaily.organization_id.isnot(None),
            )
            .group_by(
                ExecutionMetricsDaily.organization_id,
                Organization.name,
            )
            .order_by(func.sum(ExecutionMetricsDaily.total_value).desc())
            .limit(limit)
        )

        result = await db.execute(query)
        rows = result.all()

        organizations = [
            OrganizationROIEntry(
                organization_id=str(row.organization_id),
                organization_name=row.organization_name or "Unknown",
                execution_count=row.execution_count or 0,
                success_count=row.success_count or 0,
                total_time_saved=row.total_time_saved or 0,
                total_value=float(row.total_value or 0),
            )
            for row in rows
        ]

        return ROIByOrganizationResponse(
            organizations=organizations,
            time_saved_unit=settings.time_saved_unit,
            value_unit=settings.value_unit,
        )

    except Exception as e:
        logger.error(f"Error getting ROI by organization: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get ROI by organization",
        )


@router.get(
    "/trends",
    response_model=ROITrendsResponse,
    summary="Get ROI trends",
    description="Get ROI trends over time. Platform admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def get_roi_trends(
    user: CurrentActiveUser,
    db: DbSession,
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    granularity: Literal["day", "week", "month"] = Query(default="day", description="Time granularity"),
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all, 'global' for global only, "
        "or org UUID for specific org."
    ),
) -> ROITrendsResponse:
    """
    Get ROI trends over time.

    Aggregates metrics from execution_metrics_daily table with time grouping.
    Platform admin only.

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        granularity: Time granularity (day, week, month)
        scope: Filter scope - None (all), "global", or org UUID string

    Returns:
        Time series ROI data with the specified granularity
    """
    # Parse scope parameter
    org_uuid: UUID | None = None
    global_only = False
    if scope:
        if scope == "global":
            global_only = True
        else:
            try:
                org_uuid = UUID(scope)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid scope value: {scope}",
                )

    try:
        # Get ROI settings for units
        settings_service = ROISettingsService(db)
        settings = await settings_service.get_settings()

        # Determine date grouping based on granularity
        if granularity == "day":
            date_group = ExecutionMetricsDaily.date
        elif granularity == "week":
            # PostgreSQL: date_trunc('week', date)
            date_group = func.date_trunc("week", ExecutionMetricsDaily.date)
        else:  # month
            # PostgreSQL: date_trunc('month', date)
            date_group = func.date_trunc("month", ExecutionMetricsDaily.date)

        # Build query
        query = (
            select(
                date_group.label("period"),
                func.sum(ExecutionMetricsDaily.execution_count).label("execution_count"),
                func.sum(ExecutionMetricsDaily.success_count).label("success_count"),
                func.sum(ExecutionMetricsDaily.total_time_saved).label("time_saved"),
                func.sum(ExecutionMetricsDaily.total_value).label("value"),
            )
            .where(
                ExecutionMetricsDaily.date >= start_date,
                ExecutionMetricsDaily.date <= end_date,
            )
            .group_by(date_group)
            .order_by(date_group)
        )

        # Apply organization filter from scope param
        if global_only:
            query = query.where(ExecutionMetricsDaily.organization_id.is_(None))
        elif org_uuid:
            query = query.where(ExecutionMetricsDaily.organization_id == org_uuid)
        # If no filter, return all metrics (platform admins see everything)

        result = await db.execute(query)
        rows = result.all()

        entries = [
            ROITrendEntry(
                period=row.period.isoformat() if hasattr(row.period, "isoformat") else str(row.period),
                execution_count=row.execution_count or 0,
                success_count=row.success_count or 0,
                time_saved=row.time_saved or 0,
                value=float(row.value or 0),
            )
            for row in rows
        ]

        return ROITrendsResponse(
            entries=entries,
            granularity=granularity,
            time_saved_unit=settings.time_saved_unit,
            value_unit=settings.value_unit,
        )

    except Exception as e:
        logger.error(f"Error getting ROI trends: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get ROI trends",
        )
