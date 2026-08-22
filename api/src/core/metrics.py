"""
Metrics Helper Functions

Functions for updating execution metrics on completion.
Called by the workflow execution consumer.
"""

import logging
from datetime import datetime, date, timezone
from uuid import UUID

from sqlalchemy import Integer, select, func, text, exists
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_session_factory
from src.models import ExecutionMetricsDaily, WorkflowROIDaily, Workflow
from src.models.enums import ExecutionStatus

logger = logging.getLogger(__name__)


async def update_daily_metrics(
    org_id: str | None,
    status: str,
    duration_ms: int | None = None,
    peak_memory_bytes: int | None = None,
    cpu_total_seconds: float | None = None,
    time_saved: int = 0,
    value: float = 0.0,
    workflow_id: str | None = None,
    db: AsyncSession | None = None,
) -> None:
    """
    Update daily execution metrics.

    Called on each execution completion to update the daily aggregates.
    Uses upsert to create row if not exists, then increment counters.

    Args:
        org_id: Organization ID (None for global/platform executions)
        status: Final execution status
        duration_ms: Execution duration in milliseconds
        peak_memory_bytes: Peak memory usage
        cpu_total_seconds: Total CPU time
        time_saved: Minutes saved (only counted for SUCCESS)
        value: Value generated (only counted for SUCCESS)
        workflow_id: Workflow ID for per-workflow tracking
    """
    today = date.today()
    org_uuid = (
        UUID(org_id.replace("ORG:", ""))
        if org_id and org_id.startswith("ORG:")
        else None
    )

    try:
        # Use provided session if given; otherwise open a new one
        if db is None:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await _upsert_daily_metrics(
                    session,
                    today,
                    org_uuid,
                    status,
                    duration_ms,
                    peak_memory_bytes,
                    cpu_total_seconds,
                    time_saved,
                    value,
                )

                await _upsert_daily_metrics(
                    session,
                    today,
                    None,
                    status,
                    duration_ms,
                    peak_memory_bytes,
                    cpu_total_seconds,
                    time_saved,
                    value,
                )

                await session.commit()
        else:
            # Use provided session - caller manages commit
            await _upsert_daily_metrics(
                db,
                today,
                org_uuid,
                status,
                duration_ms,
                peak_memory_bytes,
                cpu_total_seconds,
                time_saved,
                value,
            )

            await _upsert_daily_metrics(
                db,
                today,
                None,
                status,
                duration_ms,
                peak_memory_bytes,
                cpu_total_seconds,
                time_saved,
                value,
            )

    except Exception as e:
        logger.error(f"Error updating daily metrics: {e}", exc_info=True)
        # Don't raise - metrics update failure shouldn't fail the execution


async def _upsert_daily_metrics(
    db,
    today: date,
    org_id: UUID | None,
    status: str,
    duration_ms: int | None,
    peak_memory_bytes: int | None,
    cpu_total_seconds: float | None,
    time_saved: int,
    value: float,
) -> None:
    """
    Upsert a single daily metrics row.

    Uses PostgreSQL INSERT ... ON CONFLICT to atomically update.
    """
    # Determine which counter to increment based on status
    is_success = status == ExecutionStatus.SUCCESS.value
    is_failed = status == ExecutionStatus.FAILED.value
    is_timeout = status == ExecutionStatus.TIMEOUT.value
    is_cancelled = status == ExecutionStatus.CANCELLED.value

    # Only count ROI for successful executions
    add_time_saved = time_saved if is_success else 0
    add_value = value if is_success else 0.0

    # Build base values for insert
    insert_values = {
        "date": today,
        "organization_id": org_id,
        "execution_count": 1,
        "success_count": 1 if is_success else 0,
        "failed_count": 1 if is_failed else 0,
        "timeout_count": 1 if is_timeout else 0,
        "cancelled_count": 1 if is_cancelled else 0,
        "total_duration_ms": duration_ms or 0,
        "avg_duration_ms": duration_ms or 0,
        "max_duration_ms": duration_ms or 0,
        "total_memory_bytes": peak_memory_bytes or 0,
        "peak_memory_bytes": peak_memory_bytes or 0,
        "total_cpu_seconds": cpu_total_seconds or 0.0,
        "peak_cpu_seconds": cpu_total_seconds or 0.0,
        "total_time_saved": add_time_saved,
        "total_value": add_value,
    }

    # PostgreSQL upsert
    stmt = insert(ExecutionMetricsDaily).values(**insert_values)
    new_execution_count = ExecutionMetricsDaily.execution_count + 1
    new_total_duration = (
        ExecutionMetricsDaily.total_duration_ms + (duration_ms or 0)
    )
    new_avg_duration = func.floor(
        new_total_duration / new_execution_count
    ).cast(Integer)

    # On conflict, increment counters
    # Use different conflict target based on whether this is org-specific or global:
    # - org_id is not None: use named constraint "uq_metrics_daily_date_org"
    # - org_id is None (global): use index_elements with index_where for partial unique index
    #   (PostgreSQL's ON CONFLICT ON CONSTRAINT only works with constraints, not indexes)
    if org_id is not None:
        stmt = stmt.on_conflict_do_update(
            constraint="uq_metrics_daily_date_org",
            set_={
                "execution_count": ExecutionMetricsDaily.execution_count + 1,
                "success_count": ExecutionMetricsDaily.success_count
                + (1 if is_success else 0),
                "failed_count": ExecutionMetricsDaily.failed_count
                + (1 if is_failed else 0),
                "timeout_count": ExecutionMetricsDaily.timeout_count
                + (1 if is_timeout else 0),
                "cancelled_count": ExecutionMetricsDaily.cancelled_count
                + (1 if is_cancelled else 0),
                "total_duration_ms": ExecutionMetricsDaily.total_duration_ms
                + (duration_ms or 0),
                "avg_duration_ms": new_avg_duration,
                "max_duration_ms": func.greatest(
                    ExecutionMetricsDaily.max_duration_ms, duration_ms or 0
                ),
                "total_memory_bytes": ExecutionMetricsDaily.total_memory_bytes
                + (peak_memory_bytes or 0),
                "peak_memory_bytes": func.greatest(
                    ExecutionMetricsDaily.peak_memory_bytes, peak_memory_bytes or 0
                ),
                "total_cpu_seconds": ExecutionMetricsDaily.total_cpu_seconds
                + (cpu_total_seconds or 0.0),
                "peak_cpu_seconds": func.greatest(
                    ExecutionMetricsDaily.peak_cpu_seconds, cpu_total_seconds or 0.0
                ),
                "total_time_saved": ExecutionMetricsDaily.total_time_saved
                + add_time_saved,
                "total_value": ExecutionMetricsDaily.total_value + add_value,
                "updated_at": datetime.now(timezone.utc),
            },
        )
    else:
        # For global metrics (org_id IS NULL), use index_elements with index_where
        # to match the partial unique index
        stmt = stmt.on_conflict_do_update(
            index_elements=["date"],
            index_where=text("organization_id IS NULL"),
            set_={
                "execution_count": ExecutionMetricsDaily.execution_count + 1,
                "success_count": ExecutionMetricsDaily.success_count
                + (1 if is_success else 0),
                "failed_count": ExecutionMetricsDaily.failed_count
                + (1 if is_failed else 0),
                "timeout_count": ExecutionMetricsDaily.timeout_count
                + (1 if is_timeout else 0),
                "cancelled_count": ExecutionMetricsDaily.cancelled_count
                + (1 if is_cancelled else 0),
                "total_duration_ms": ExecutionMetricsDaily.total_duration_ms
                + (duration_ms or 0),
                "avg_duration_ms": new_avg_duration,
                "max_duration_ms": func.greatest(
                    ExecutionMetricsDaily.max_duration_ms, duration_ms or 0
                ),
                "total_memory_bytes": ExecutionMetricsDaily.total_memory_bytes
                + (peak_memory_bytes or 0),
                "peak_memory_bytes": func.greatest(
                    ExecutionMetricsDaily.peak_memory_bytes, peak_memory_bytes or 0
                ),
                "total_cpu_seconds": ExecutionMetricsDaily.total_cpu_seconds
                + (cpu_total_seconds or 0.0),
                "peak_cpu_seconds": func.greatest(
                    ExecutionMetricsDaily.peak_cpu_seconds, cpu_total_seconds or 0.0
                ),
                "total_time_saved": ExecutionMetricsDaily.total_time_saved
                + add_time_saved,
                "total_value": ExecutionMetricsDaily.total_value + add_value,
                "updated_at": datetime.now(timezone.utc),
            },
        )

    # The average is derived atomically in the conflict update above. Keeping
    # this to one statement minimizes the time the shared daily row is locked
    # when many executions finish together.
    await db.execute(stmt)


async def update_workflow_roi_daily(
    workflow_id: str,
    org_id: str | None,
    status: str,
    time_saved: int = 0,
    value: float = 0.0,
    db: AsyncSession | None = None,
) -> None:
    """
    Update daily workflow ROI metrics.

    Called on each execution completion to track per-workflow ROI.
    Uses upsert pattern to create row if not exists, then increment counters.

    Args:
        workflow_id: Workflow ID
        org_id: Organization ID (None for global/platform executions)
        status: Final execution status
        time_saved: Minutes saved (only counted for SUCCESS)
        value: Value generated (only counted for SUCCESS)
        db: Optional database session
    """
    logger.debug(f"ROI update called: workflow_id={workflow_id}, org_id={org_id}, status={status}, db_provided={db is not None}")
    today = date.today()
    workflow_uuid = UUID(workflow_id)
    org_uuid = (
        UUID(org_id.replace("ORG:", ""))
        if org_id and org_id.startswith("ORG:")
        else None
    )

    def _is_missing_workflow_fk_violation(exc: IntegrityError) -> bool:
        orig = getattr(exc, "orig", None)
        orig_name = getattr(getattr(orig, "__class__", None), "__name__", "")
        if orig_name != "ForeignKeyViolationError":
            return False
        constraint_name = getattr(orig, "constraint_name", None)
        if constraint_name in {
            # Legacy name from before workflow_economics_daily -> workflow_roi_daily rename
            "workflow_economics_daily_workflow_id_fkey",
            "workflow_roi_daily_workflow_id_fkey",
        }:
            return True
        message = str(orig) if orig is not None else str(exc)
        return (
            "violates foreign key constraint" in message
            and "workflow" in message
            and ("workflows" in message or "workflow_id" in message)
        )

    # Check if workflow still exists (may have been deleted by test cleanup or user)
    async def _workflow_exists(session: AsyncSession) -> bool:
        stmt = select(exists().where(Workflow.id == workflow_uuid))
        result = await session.execute(stmt)
        exists_result = result.scalar() or False
        logger.debug(f"ROI workflow exists check: workflow_id={workflow_id}, exists={exists_result}")
        return exists_result

    try:
        # Verify workflow exists before attempting ROI upsert
        if db is None:
            session_factory = get_session_factory()
            async with session_factory() as session:
                workflow_exists = await _workflow_exists(session)
                logger.debug(f"ROI existence check (new session): workflow_id={workflow_id}, exists={workflow_exists}")
                if not workflow_exists:
                    logger.debug(
                        f"Skipping ROI update - workflow {workflow_id} no longer exists"
                    )
                    return
        else:
            workflow_exists = await _workflow_exists(db)
            logger.debug(f"ROI existence check (provided session): workflow_id={workflow_id}, exists={workflow_exists}")
            if not workflow_exists:
                logger.debug(
                    f"Skipping ROI update - workflow {workflow_id} no longer exists"
                )
                return
    except Exception as e:
        logger.warning(f"Skipping ROI update - failed to check workflow existence: {e}")
        return

    logger.debug(f"ROI proceeding with upsert: workflow_id={workflow_id}")

    # Only count ROI for successful executions
    is_success = status == ExecutionStatus.SUCCESS.value
    add_time_saved = time_saved if is_success else 0
    add_value = value if is_success else 0.0

    try:
        # Use provided session if given; otherwise open a new one
        if db is None:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await _upsert_workflow_roi(
                    session,
                    today,
                    workflow_uuid,
                    org_uuid,
                    is_success,
                    add_time_saved,
                    add_value,
                )
                await session.commit()
        else:
            # Use provided session - caller manages commit
            await _upsert_workflow_roi(
                db,
                today,
                workflow_uuid,
                org_uuid,
                is_success,
                add_time_saved,
                add_value,
            )

    except IntegrityError as e:
        orig = getattr(e, "orig", None)
        orig_class = getattr(getattr(orig, "__class__", None), "__name__", "unknown")
        constraint_name = getattr(orig, "constraint_name", "unknown")
        logger.warning(f"ROI IntegrityError caught: workflow_id={workflow_id}, orig_class={orig_class}, constraint={constraint_name}")
        if _is_missing_workflow_fk_violation(e):
            logger.warning(
                f"Skipping ROI update - workflow {workflow_id} not found (likely deleted during execution cleanup)"
            )
            if db is not None:
                await db.rollback()
            return
        logger.error(
            f"Integrity error updating workflow ROI (not FK violation): {e}",
            exc_info=True,
        )
        if db is not None:
            await db.rollback()
    except Exception as e:
        logger.error(
            f"Error updating workflow ROI: {e}",
            exc_info=True,
        )
        if db is not None:
            await db.rollback()
        # Don't raise - metrics update failure shouldn't fail the execution


async def _upsert_workflow_roi(
    db,
    today: date,
    workflow_id: UUID,
    org_id: UUID | None,
    is_success: bool,
    time_saved: int,
    value: float,
) -> None:
    """
    Upsert a single workflow ROI row.

    Uses PostgreSQL INSERT ... ON CONFLICT to atomically update.

    There are two unique constraints:
    - uq_workflow_roi_daily: (date, workflow_id, organization_id) for org-scoped rows
    - uq_workflow_roi_daily_date_workflow_global: partial index on (date, workflow_id)
      WHERE organization_id IS NULL for global rows
    """
    from sqlalchemy import text

    # Build base values for insert
    insert_values = {
        "date": today,
        "workflow_id": workflow_id,
        "organization_id": org_id,
        "execution_count": 1,
        "success_count": 1 if is_success else 0,
        "total_time_saved": time_saved,
        "total_value": value,
    }

    # PostgreSQL upsert
    stmt = insert(WorkflowROIDaily).values(**insert_values)

    # Update values on conflict
    update_set = {
        "execution_count": WorkflowROIDaily.execution_count + 1,
        "success_count": WorkflowROIDaily.success_count + (1 if is_success else 0),
        "total_time_saved": WorkflowROIDaily.total_time_saved + time_saved,
        "total_value": WorkflowROIDaily.total_value + value,
        "updated_at": datetime.now(timezone.utc),
    }

    # Use different conflict target based on whether org_id is NULL
    if org_id is None:
        # For global metrics (org_id IS NULL), use the partial unique index
        stmt = stmt.on_conflict_do_update(
            index_elements=["date", "workflow_id"],
            index_where=text("organization_id IS NULL"),
            set_=update_set,
        )
    else:
        # For org-scoped metrics, use the 3-column constraint
        stmt = stmt.on_conflict_do_update(
            constraint="uq_workflow_roi_daily",
            set_=update_set,
        )

    await db.execute(stmt)
