"""
Metrics Refresh Scheduler

Refreshes the platform_metrics_snapshot table with current metrics.
Runs hourly to keep dashboard data fresh without expensive queries.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func, case, update

from src.core.database import get_session_factory
from src.models import (
    Execution as ExecutionModel,
    Workflow,
    Form,
    Organization,
    User,
    PlatformMetricsSnapshot,
    ExecutionMetricsDaily,
)
from src.models.enums import ExecutionStatus
from src.models.orm.ai_usage import AIUsage

logger = logging.getLogger(__name__)


async def refresh_metrics_snapshot() -> dict[str, Any]:
    """
    Refresh the platform metrics snapshot.

    Aggregates current metrics and updates the snapshot table.
    This runs periodically so dashboard loads are instant.

    Returns:
        Summary of refreshed metrics
    """
    logger.info("▶ Metrics snapshot refresh starting")
    start_time = datetime.now(timezone.utc)

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)

    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            # Entity counts (single query with subqueries)
            # Note: Data providers are now in the workflows table with type='data_provider'
            counts_query = select(
                select(func.count(Workflow.id))
                .where(Workflow.is_active.is_(True))
                .where(Workflow.type.not_in(["data_provider", "service"]))  # Exclude providers and services
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
                select(func.count(Organization.id))
                .where(Organization.is_active.is_(True))
                .correlate(None)
                .scalar_subquery()
                .label("org_count"),
                select(func.count(User.id))
                .where(User.is_active.is_(True))
                .correlate(None)
                .scalar_subquery()
                .label("user_count"),
            )
            counts_result = await db.execute(counts_query)
            counts = counts_result.one()

            # All-time execution stats
            all_time_query = select(
                func.count(ExecutionModel.id).label("total"),
                func.sum(case((ExecutionModel.status == ExecutionStatus.SUCCESS.value, 1), else_=0)).label("success"),
                func.sum(case((ExecutionModel.status == ExecutionStatus.FAILED.value, 1), else_=0)).label("failed"),
            )
            all_time_result = await db.execute(all_time_query)
            all_time = all_time_result.one()

            # Last 24 hours execution stats
            last_24h_query = select(
                func.count(ExecutionModel.id).label("total"),
                func.sum(case((ExecutionModel.status == ExecutionStatus.SUCCESS.value, 1), else_=0)).label("success"),
                func.sum(case((ExecutionModel.status == ExecutionStatus.FAILED.value, 1), else_=0)).label("failed"),
                func.avg(case((ExecutionModel.status == ExecutionStatus.SUCCESS.value, ExecutionModel.duration_ms), else_=None)).label("avg_duration"),
                func.sum(case((ExecutionModel.peak_memory_bytes.isnot(None), ExecutionModel.peak_memory_bytes), else_=0)).label("total_memory"),
                func.sum(case((ExecutionModel.cpu_total_seconds.isnot(None), ExecutionModel.cpu_total_seconds), else_=0)).label("total_cpu"),
            ).where(ExecutionModel.created_at >= yesterday)
            last_24h_result = await db.execute(last_24h_query)
            last_24h = last_24h_result.one()

            # Current running/pending counts
            current_query = select(
                func.sum(case((ExecutionModel.status == ExecutionStatus.RUNNING.value, 1), else_=0)).label("running"),
                func.sum(case((ExecutionModel.status == ExecutionStatus.PENDING.value, 1), else_=0)).label("pending"),
            )
            current_result = await db.execute(current_query)
            current = current_result.one()

            # ROI (last 24 hours) - from execution_metrics_daily table
            roi_24h_query = select(
                func.sum(ExecutionMetricsDaily.total_time_saved).label("time_saved"),
                func.sum(ExecutionMetricsDaily.total_value).label("value"),
            ).where(
                ExecutionMetricsDaily.date >= yesterday.date(),
                ExecutionMetricsDaily.organization_id.is_(None),  # Global metrics only
            )
            roi_24h_result = await db.execute(roi_24h_query)
            roi_24h = roi_24h_result.one()

            # ROI (all time) - from execution_metrics_daily table
            roi_all_time_query = select(
                func.sum(ExecutionMetricsDaily.total_time_saved).label("time_saved"),
                func.sum(ExecutionMetricsDaily.total_value).label("value"),
            ).where(
                ExecutionMetricsDaily.organization_id.is_(None),  # Global metrics only
            )
            roi_all_time_result = await db.execute(roi_all_time_query)
            roi_all_time = roi_all_time_result.one()

            # AI usage (last 24 hours) - from ai_usage table
            ai_24h_query = select(
                func.sum(AIUsage.cost).label("total_cost"),
                func.count(AIUsage.id).label("total_calls"),
            ).where(AIUsage.timestamp >= yesterday)
            ai_24h_result = await db.execute(ai_24h_query)
            ai_24h = ai_24h_result.one()

            # Calculate success rates
            total_all_time = all_time.total or 0
            success_all_time = all_time.success or 0
            success_rate_all_time = (success_all_time / total_all_time * 100) if total_all_time > 0 else 0.0

            total_24h = last_24h.total or 0
            success_24h = last_24h.success or 0
            success_rate_24h = (success_24h / total_24h * 100) if total_24h > 0 else 0.0

            # Update snapshot
            await db.execute(
                update(PlatformMetricsSnapshot)
                .where(PlatformMetricsSnapshot.id == 1)
                .values(
                    # Entity counts
                    workflow_count=counts.workflow_count or 0,
                    form_count=counts.form_count or 0,
                    data_provider_count=counts.provider_count or 0,
                    organization_count=counts.org_count or 0,
                    user_count=counts.user_count or 0,
                    # All time
                    total_executions=total_all_time,
                    total_success=success_all_time,
                    total_failed=all_time.failed or 0,
                    # Last 24 hours
                    executions_24h=total_24h,
                    success_24h=success_24h,
                    failed_24h=last_24h.failed or 0,
                    # Current state
                    running_count=current.running or 0,
                    pending_count=current.pending or 0,
                    # Performance
                    avg_duration_ms_24h=int(last_24h.avg_duration or 0),
                    total_memory_bytes_24h=int(last_24h.total_memory or 0),
                    total_cpu_seconds_24h=float(last_24h.total_cpu or 0),
                    # Success rates
                    success_rate_all_time=round(success_rate_all_time, 2),
                    success_rate_24h=round(success_rate_24h, 2),
                    # ROI (last 24 hours)
                    time_saved_24h=int(roi_24h.time_saved or 0),
                    value_24h=float(roi_24h.value or 0),
                    # ROI (all time)
                    time_saved_all_time=int(roi_all_time.time_saved or 0),
                    value_all_time=float(roi_all_time.value or 0),
                    # AI usage (last 24 hours)
                    ai_cost_24h=ai_24h.total_cost,
                    ai_calls_24h=ai_24h.total_calls or 0,
                    # Timestamp
                    refreshed_at=now,
                )
            )
            await db.commit()

            result = {
                "workflow_count": counts.workflow_count or 0,
                "form_count": counts.form_count or 0,
                "organization_count": counts.org_count or 0,
                "total_executions": total_all_time,
                "executions_24h": total_24h,
                "success_rate_24h": round(success_rate_24h, 2),
                "ai_cost_24h": float(ai_24h.total_cost) if ai_24h.total_cost else 0.0,
                "ai_calls_24h": ai_24h.total_calls or 0,
                "refreshed_at": now.isoformat(),
            }

            duration_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info(
                f"✓ Metrics snapshot refreshed: "
                f"{total_all_time} total executions, {total_24h} in 24h ({duration_seconds:.1f}s)"
            )

            return result

    except Exception as e:
        logger.error(f"✗ Metrics snapshot refresh failed: {e}", exc_info=True)
        return {"error": str(e)}
