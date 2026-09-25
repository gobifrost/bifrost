"""
Executions Router

Provides access to workflow execution history with filtering capabilities.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Import existing Pydantic models for API compatibility
from src.models import (
    ExecutionStatus,
    ExecutionsListResponse,
    WorkflowExecution,
    StuckExecutionsResponse,
    CleanupTriggeredResponse,
    ExecutionLogPublic,
)
from src.models.contracts.executions import (
    LogsListResponse,
    LogListEntry,
)

from bifrost._logging import read_logs_from_stream
from shared.sdk_execution_reads import decode_history_cursor
from src.core.auth import Context, RequirePlatformAdmin
from src.core.principal import UserPrincipal
from src.core.log_safety import log_safe
from src.core.pubsub import publish_execution_update, publish_history_update
from src.core.redis_client import get_redis_client
from src.models import Execution as ExecutionModel
from src.models import ExecutionLog as ExecutionLogORM
from src.repositories.execution_logs import (
    ExecutionLogRepository,
    decode_execution_log_cursor,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/executions", tags=["Executions"])

_EXECUTION_QUERY_PARAM_ALIASES: dict[str, tuple[str, ...]] = {
    "workflowName": ("workflow_name",),
    "workflowId": ("workflow_id",),
    "startDate": ("start_date",),
    "endDate": ("end_date",),
    "excludeLocal": ("exclude_local",),
    "continuationToken": ("continuation_token",),
}

_EXECUTION_QUERY_PARAM_NAMES = {
    "scope",
    "workflowName",
    "workflowId",
    "status",
    "startDate",
    "endDate",
    "excludeLocal",
    "limit",
    "continuationToken",
    *{alias for aliases in _EXECUTION_QUERY_PARAM_ALIASES.values() for alias in aliases},
}


def _query_param(request: Request, name: str) -> str | None:
    for key in (name, *_EXECUTION_QUERY_PARAM_ALIASES.get(name, ())):
        value = request.query_params.get(key)
        if value is not None:
            return value
    return None


def _reject_unknown_query_params(request: Request) -> None:
    unknown = sorted(set(request.query_params.keys()) - _EXECUTION_QUERY_PARAM_NAMES)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported query parameter(s): {', '.join(unknown)}",
        )


def _validate_iso_date_filter(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{name} must be an ISO 8601 date-time",
        ) from exc
    return value


# =============================================================================
# Repository
# =============================================================================


class ExecutionRepository:
    """PostgreSQL-based execution repository."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_execution_result(
        self,
        execution_id: UUID,
        user: UserPrincipal,
    ) -> tuple[Any, str | None]:
        """Get execution result only."""
        result = await self.db.execute(
            select(
                ExecutionModel.result,
                ExecutionModel.result_type,
                ExecutionModel.executed_by,
            ).where(ExecutionModel.id == execution_id)
        )
        row = result.one_or_none()

        if not row:
            return None, "NotFound"

        if not user.is_superuser and row.executed_by != user.user_id:
            return None, "Forbidden"

        return {"result": row.result, "result_type": row.result_type}, None

    async def get_execution_logs(
        self,
        execution_id: UUID,
        user: UserPrincipal,
    ) -> tuple[list[ExecutionLogPublic] | None, str | None]:
        """Get execution logs — dual-read from Redis Stream when in-progress, DB when complete."""
        # Check if execution exists, user has access, and get status
        result = await self.db.execute(
            select(ExecutionModel.executed_by, ExecutionModel.status)
            .where(ExecutionModel.id == execution_id)
        )
        row = result.one_or_none()

        if not row:
            return None, "NotFound"

        if not user.is_superuser and row.executed_by != user.user_id:
            return None, "Forbidden"

        is_in_progress = row.status in (
            ExecutionStatus.PENDING, ExecutionStatus.RUNNING,
        )

        if is_in_progress:
            # Read from Redis Stream
            try:
                stream_logs = await read_logs_from_stream(str(execution_id), count=10000)
                hidden_levels = set() if user.is_superuser else {"DEBUG", "TRACEBACK"}
                logs: list[ExecutionLogPublic] = []
                for seq, slog in enumerate(stream_logs):
                    level = (slog.level or "INFO").upper()
                    if level in hidden_levels:
                        continue
                    logs.append(ExecutionLogPublic(
                        id=seq,
                        timestamp=slog.timestamp or "",
                        level=level.lower(),
                        message=slog.message or "",
                        data=slog.metadata if isinstance(slog.metadata, dict) else None,
                        sequence=seq,
                    ))
                return logs, None
            except Exception:
                logger.warning(f"Failed to read logs from Redis for execution {log_safe(execution_id)}, falling back to DB")

        # Completed or Redis failed — read from Postgres
        logs_query = (
            select(ExecutionLogORM)
            .where(ExecutionLogORM.execution_id == execution_id)
            .order_by(ExecutionLogORM.sequence)
        )
        if not user.is_superuser:
            logs_query = logs_query.where(ExecutionLogORM.level.notin_(["DEBUG", "TRACEBACK"]))

        logs_result = await self.db.execute(logs_query)
        log_entries = logs_result.scalars().all()

        logs = [
            ExecutionLogPublic(
                id=log.id,
                timestamp=log.timestamp.isoformat() if log.timestamp else "",
                level=log.level or "info",
                message=log.message or "",
                data=log.log_metadata,
                sequence=log.sequence,
            )
            for log in log_entries
        ]

        return logs, None

    async def get_execution_variables(
        self,
        execution_id: UUID,
        user: UserPrincipal,
    ) -> tuple[dict | None, str | None]:
        """Get execution variables (platform admin only)."""
        if not user.is_superuser:
            return None, "Forbidden"

        # Select id and variables to distinguish "not found" from "null variables"
        result = await self.db.execute(
            select(ExecutionModel.id, ExecutionModel.variables)
            .where(ExecutionModel.id == execution_id)
        )
        row = result.one_or_none()

        if row is None:
            return None, "NotFound"

        # row is a tuple of (id, variables)
        return row[1] or {}, None

    async def cancel_execution(
        self,
        execution_id: UUID,
        user: UserPrincipal,
    ) -> tuple[WorkflowExecution | None, str | None]:
        """Cancel a pending or running execution."""
        result = await self.db.execute(
            select(ExecutionModel)
            .options(selectinload(ExecutionModel.organization), selectinload(ExecutionModel.executed_by_user))
            .where(ExecutionModel.id == execution_id)
        )
        execution = result.scalar_one_or_none()

        if not execution:
            return None, "NotFound"

        if not user.is_superuser and execution.executed_by != user.user_id:
            return None, "Forbidden"

        # Already cancelled or cancelling - idempotent success
        if execution.status in [ExecutionStatus.CANCELLING.value, ExecutionStatus.CANCELLED.value]:
            return self._to_pydantic(execution, user), None

        # Can only cancel pending or running executions
        if execution.status not in [ExecutionStatus.PENDING.value, ExecutionStatus.RUNNING.value]:
            return None, "BadRequest"

        # For PENDING executions, cancel immediately - no worker has started it yet
        # For RUNNING executions, set to CANCELLING and let the worker handle it
        if execution.status == ExecutionStatus.PENDING.value:
            new_status = ExecutionStatus.CANCELLED.value
        else:
            new_status = ExecutionStatus.CANCELLING.value

        execution.status = new_status  # type: ignore[assignment]

        await self.db.flush()
        await self.db.refresh(execution)

        # Publish update
        await publish_execution_update(
            execution_id=execution_id,
            status=new_status,
        )
        await publish_history_update(
            execution_id=execution_id,
            status=new_status,
            executed_by=execution.executed_by,
            executed_by_name=execution.executed_by_name,
            workflow_name=execution.workflow_name,
            org_id=execution.organization_id,
            started_at=execution.started_at,
        )

        return self._to_pydantic(execution, user), None

    @staticmethod
    def _org_name(execution: ExecutionModel) -> str | None:
        """Display name for the execution's effective scope."""
        if execution.organization_id:
            if hasattr(execution, 'organization') and execution.organization is not None:
                return execution.organization.name
            return None  # Will be populated by caller if needed
        return "Global"  # No org_id means global scope

    def _to_pydantic(
        self, execution: ExecutionModel, user: UserPrincipal | None = None
    ) -> WorkflowExecution:
        """Convert SQLAlchemy model to Pydantic model.

        Note: logs are NOT included here - they should be fetched separately
        via the /logs endpoint to avoid loading potentially large log data.

        Args:
            execution: The SQLAlchemy execution model
            user: Optional user for permission checks. If provided, admin-only
                  fields (variables) are gated based on is_superuser.
        """
        is_admin = user.is_superuser if user else False

        return WorkflowExecution(
            execution_id=str(execution.id),
            workflow_name=execution.workflow_name,
            workflow_id=str(execution.workflow_id) if execution.workflow_id else None,
            org_id=str(execution.organization_id) if execution.organization_id else None,
            org_name=self._org_name(execution),
            form_id=str(execution.form_id) if execution.form_id else None,
            executed_by=str(execution.executed_by),
            executed_by_name=execution.executed_by_name or str(execution.executed_by),
            executed_by_email=execution.executed_by_user.email if hasattr(execution, 'executed_by_user') and execution.executed_by_user else None,
            status=ExecutionStatus(execution.status),
            input_data=execution.parameters or {},
            result=execution.result,
            result_type=execution.result_type,
            error_message=execution.error_message,
            duration_ms=execution.duration_ms,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            scheduled_at=execution.scheduled_at,
            created_at=execution.created_at,
            logs=None,  # Fetched separately via /logs endpoint
            variables=execution.variables if is_admin else None,
            execution_context=execution.execution_context if is_admin else None,
            session_id=str(execution.session_id) if execution.session_id else None,
        )


# =============================================================================
# HTTP Endpoints
# =============================================================================


@router.get(
    "",
    response_model=ExecutionsListResponse,
    summary="List workflow executions",
    description="List workflow executions with filtering and pagination",
)
async def list_executions(
    ctx: Context,
    request: Request,
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org + global."
    ),
    workflowName: str | None = Query(None, description="Filter by workflow name"),
    workflowId: str | None = Query(None, description="Filter by workflow UUID"),
    status_filter: str | None = Query(None, alias="status", description="Filter by execution status (comma-separated values match any)"),
    startDate: str | None = Query(None, description="Filter by start date (ISO format)"),
    endDate: str | None = Query(None, description="Filter by end date (ISO format)"),
    excludeLocal: bool = Query(True, description="Exclude local runner executions"),
    limit: int = Query(25, ge=1, le=1000, description="Maximum number of results"),
    continuationToken: str | None = Query(None, description="Continuation token"),
) -> ExecutionsListResponse:
    """List workflow executions.

    Superusers can filter by scope or see all executions.
    Org users see only their organization's executions.
    """
    from shared.sdk_execution_reads import SdkExecutionReadError, list_sdk_executions

    _reject_unknown_query_params(request)

    # Parse continuation token: keyset cursor, with legacy numeric-offset
    # fallback for tokens minted before the keyset change.
    offset = 0
    cursor = None
    continuation_token = _query_param(request, "continuationToken") or continuationToken
    if continuation_token:
        cursor = decode_history_cursor(continuation_token)
        if cursor is None:
            try:
                offset = int(continuation_token)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="continuationToken is invalid",
                ) from exc
            if offset < 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="continuationToken is invalid",
                )

    # Parse workflowId to UUID if provided
    workflow_id_value = _query_param(request, "workflowId") or workflowId
    try:
        parsed_workflow_id = UUID(workflow_id_value) if workflow_id_value else None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="workflowId must be a UUID",
        ) from exc

    start_date_value = _validate_iso_date_filter(
        _query_param(request, "startDate") or startDate,
        "startDate",
    )
    end_date_value = _validate_iso_date_filter(
        _query_param(request, "endDate") or endDate,
        "endDate",
    )
    workflow_name_value = _query_param(request, "workflowName") or workflowName
    exclude_local_value = _query_param(request, "excludeLocal")
    if exclude_local_value is None:
        parsed_exclude_local = excludeLocal
    elif exclude_local_value.casefold() in {"1", "true", "yes", "on"}:
        parsed_exclude_local = True
    elif exclude_local_value.casefold() in {"0", "false", "no", "off"}:
        parsed_exclude_local = False
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="excludeLocal must be a boolean",
        )

    try:
        executions, next_token = await list_sdk_executions(
            ctx.db,
            ctx.user,
            scope=scope,
            workflow_name=workflow_name_value,
            workflow_id=parsed_workflow_id,
            status_filter=status_filter,
            start_date=start_date_value,
            end_date=end_date_value,
            exclude_local=parsed_exclude_local,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
    except SdkExecutionReadError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        )

    return ExecutionsListResponse(
        executions=executions,
        continuation_token=next_token,
    )


@router.get(
    "/logs",
    response_model=LogsListResponse,
    summary="List execution logs (admin only)",
    description="List logs across all executions with filtering and pagination. Admin only.",
    dependencies=[RequirePlatformAdmin],
)
async def list_logs(
    ctx: Context,
    organization_id: UUID | None = Query(None, description="Filter by organization"),
    global_only: bool = Query(False, description="Include only global executions"),
    workflow_id: UUID | None = Query(None, description="Filter by exact workflow ID"),
    workflow_name: str | None = Query(None, description="Filter by workflow name (partial match)"),
    levels: str | None = Query(None, description="Comma-separated log levels (e.g., ERROR,WARNING)"),
    message_search: str | None = Query(None, description="Search in log message content"),
    start_date: str | None = Query(None, description="Filter logs after this date (ISO format)"),
    end_date: str | None = Query(None, description="Filter logs before this date (ISO format)"),
    limit: int = Query(50, ge=1, le=500, description="Number of logs per page"),
    continuation_token: str | None = Query(None, description="Pagination token"),
) -> LogsListResponse:
    """List logs across all executions (admin only)."""
    # Parse continuation token: keyset cursor, with legacy numeric-offset
    # fallback for tokens minted before the keyset change.
    offset = 0
    cursor = None
    if continuation_token:
        cursor = decode_execution_log_cursor(continuation_token)
        if cursor is None:
            try:
                offset = int(continuation_token)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="continuation_token is invalid",
                ) from exc
            if offset < 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="continuation_token is invalid",
                )

    # Parse levels
    level_list = None
    if levels:
        level_list = [lvl.strip().upper() for lvl in levels.split(",")]

    # Parse dates
    parsed_start = None
    parsed_end = None
    if start_date:
        parsed_start = datetime.fromisoformat(start_date.replace("Z", "+00:00")).replace(tzinfo=None)
    if end_date:
        parsed_end = datetime.fromisoformat(end_date.replace("Z", "+00:00")).replace(tzinfo=None)

    # Get logs from repository
    logs_repo = ExecutionLogRepository(ctx.db)
    logs, next_token = await logs_repo.list_logs(
        organization_id=organization_id,
        workflow_name=workflow_name,
        workflow_id=workflow_id,
        global_only=global_only,
        levels=level_list,
        message_search=message_search,
        start_date=parsed_start,
        end_date=parsed_end,
        limit=limit,
        offset=offset,
        cursor=cursor,
    )

    return LogsListResponse(
        logs=[LogListEntry(**log) for log in logs],
        continuation_token=next_token,
    )


@router.get(
    "/{execution_id}",
    response_model=WorkflowExecution,
    summary="Get execution details",
    description="Get detailed information about a specific execution",
)
async def get_execution(
    execution_id: UUID,
    ctx: Context,
) -> WorkflowExecution:
    """Get execution details."""
    from shared.sdk_execution_reads import SdkExecutionReadError, get_sdk_execution

    try:
        return await get_sdk_execution(ctx.db, ctx.user, execution_id)
    except SdkExecutionReadError as e:
        if e.status_code == 403:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view this execution",
            )
        if e.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id} not found",
            )
        raise


@router.get(
    "/{execution_id}/result",
    summary="Get execution result only",
    description="Get only the result of a specific execution (progressive loading)",
)
async def get_execution_result(
    execution_id: UUID,
    ctx: Context,
) -> Any:
    """Get execution result."""
    repo = ExecutionRepository(ctx.db)
    result, error = await repo.get_execution_result(execution_id, ctx.user)

    if error == "NotFound":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id} not found",
        )
    elif error == "Forbidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view this execution",
        )

    return result


@router.get(
    "/{execution_id}/logs",
    summary="Get execution logs only",
    description="Get only the logs of a specific execution (progressive loading)",
    response_model=list[ExecutionLogPublic],
)
async def get_execution_logs(
    execution_id: UUID,
    ctx: Context,
) -> list[ExecutionLogPublic]:
    """Get execution logs."""
    repo = ExecutionRepository(ctx.db)
    logs, error = await repo.get_execution_logs(execution_id, ctx.user)

    if error == "NotFound":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id} not found",
        )
    elif error == "Forbidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view this execution",
        )

    return logs or []


@router.get(
    "/{execution_id}/variables",
    summary="Get execution variables only",
    description="Get only the variables of a specific execution (platform admin only)",
)
async def get_execution_variables(
    execution_id: UUID,
    ctx: Context,
) -> dict:
    """Get execution variables."""
    repo = ExecutionRepository(ctx.db)
    variables, error = await repo.get_execution_variables(execution_id, ctx.user)

    if error == "NotFound":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Execution {execution_id} not found",
        )
    elif error == "Forbidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin privileges required",
        )

    return variables or {}


@router.post(
    "/{execution_id}/cancel",
    response_model=WorkflowExecution | dict,
    summary="Cancel execution",
    description="Cancel a pending or running execution",
)
async def cancel_execution(
    execution_id: UUID,
    ctx: Context,
) -> WorkflowExecution | dict:
    """
    Cancel an execution.

    With Redis-first architecture, the execution may be in one of three states:
    1. In Redis pending only (worker hasn't picked it up yet)
    2. In PostgreSQL as Running (worker is executing)
    3. In PostgreSQL as completed (already done - return error)

    We handle all cases by:
    1. First check PostgreSQL for authorization and status
    2. If found and Running/Pending, set Redis cancel flag for pool
    3. If not in PostgreSQL yet, try to cancel Redis pending record
    """
    redis_client = get_redis_client()
    repo = ExecutionRepository(ctx.db)

    # First, try to find in PostgreSQL to check authorization
    execution, error = await repo.cancel_execution(execution_id, ctx.user)

    if error == "Forbidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to cancel this execution",
        )
    elif error == "BadRequest":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel execution {execution_id} - must be Pending or Running",
        )
    elif execution is not None:
        # Found in PostgreSQL and status was updated
        # Only publish cancel event if execution is CANCELLING (was RUNNING)
        # PENDING executions are directly set to CANCELLED - no worker to notify
        if execution.status == ExecutionStatus.CANCELLING:
            # Set Redis cancel flag so the execution pool can terminate the worker
            await redis_client.set_cancel_flag(str(execution_id))
            # Publish cancel event via pub/sub for immediate process termination
            await redis_client.publish_cancel_event(str(execution_id))
            logger.info(f"Set cancel flag and published event for execution: {log_safe(execution_id)}")
        else:
            logger.info(f"Execution {log_safe(execution_id)} was PENDING, cancelled directly in DB")
        return execution

    # Not found in PostgreSQL - check if it's still pending in Redis
    # (worker hasn't created the PostgreSQL record yet)
    pending_cancelled = await redis_client.set_pending_cancelled(str(execution_id))

    if pending_cancelled:
        # Execution was in Redis pending, now marked as cancelled
        # Worker will see this flag and skip execution
        logger.info(f"Cancelled pending execution in Redis: {log_safe(execution_id)}")
        return {
            "execution_id": str(execution_id),
            "status": "Cancelled",
            "message": "Execution cancelled before it started",
        }

    # Not found anywhere
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Execution {execution_id} not found",
    )


# =============================================================================
# Cleanup Endpoints
# =============================================================================


@router.get(
    "/cleanup/stuck",
    response_model=StuckExecutionsResponse,
    summary="Get stuck executions",
    description="Get executions that have been running, pending, or cancelling too long (Platform admin only)",
    dependencies=[RequirePlatformAdmin],
)
async def get_stuck_executions(
    ctx: Context,
    hours: int = Query(24, description="Hours since start to consider stuck"),
) -> StuckExecutionsResponse:
    """Get stuck executions that may need cleanup."""

    # Find executions that have been pending/running/cancelling for too long
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    query = select(ExecutionModel).options(
        selectinload(ExecutionModel.organization),
        selectinload(ExecutionModel.executed_by_user),
    ).where(
        and_(
            ExecutionModel.status.in_([
                ExecutionStatus.PENDING.value,
                ExecutionStatus.RUNNING.value,
                ExecutionStatus.CANCELLING.value,
            ]),
            ExecutionModel.started_at < cutoff,
        )
    ).order_by(desc(ExecutionModel.started_at))

    result = await ctx.db.execute(query)
    executions = result.scalars().all()

    repo = ExecutionRepository(ctx.db)
    stuck_executions = [repo._to_pydantic(e, ctx.user) for e in executions]

    return StuckExecutionsResponse(
        executions=stuck_executions,
        count=len(stuck_executions),
    )


@router.post(
    "/cleanup/trigger",
    response_model=CleanupTriggeredResponse,
    summary="Trigger execution cleanup",
    description="Clean up stuck executions by marking them as timed out or cancelled (Platform admin only)",
    dependencies=[RequirePlatformAdmin],
)
async def trigger_cleanup(
    ctx: Context,
    hours: int = Query(24, description="Hours since start to consider stuck"),
) -> CleanupTriggeredResponse:
    """Trigger cleanup of stuck executions."""
    # Find stuck executions
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Count pending
    pending_query = select(ExecutionModel).where(
        and_(
            ExecutionModel.status == ExecutionStatus.PENDING.value,
            ExecutionModel.started_at < cutoff,
        )
    )
    pending_result = await ctx.db.execute(pending_query)
    pending_executions = pending_result.scalars().all()

    # Count running
    running_query = select(ExecutionModel).where(
        and_(
            ExecutionModel.status == ExecutionStatus.RUNNING.value,
            ExecutionModel.started_at < cutoff,
        )
    )
    running_result = await ctx.db.execute(running_query)
    running_executions = running_result.scalars().all()

    # Count cancelling (stuck in cancelling state)
    cancelling_query = select(ExecutionModel).where(
        and_(
            ExecutionModel.status == ExecutionStatus.CANCELLING.value,
            ExecutionModel.started_at < cutoff,
        )
    )
    cancelling_result = await ctx.db.execute(cancelling_query)
    cancelling_executions = cancelling_result.scalars().all()

    # Update all stuck executions
    failed_count = 0
    now = datetime.now(timezone.utc)

    # PENDING and RUNNING -> FAILED with timeout message
    for execution in list(pending_executions) + list(running_executions):
        try:
            execution.status = ExecutionStatus.FAILED.value  # type: ignore[assignment]
            execution.error_message = f"Execution timed out after {hours} hours"
            execution.completed_at = now
        except Exception as e:
            logger.error(f"Failed to cleanup execution {execution.id}: {e}")
            failed_count += 1

    # CANCELLING -> CANCELLED (they were being cancelled but got stuck)
    for execution in cancelling_executions:
        try:
            execution.status = ExecutionStatus.CANCELLED.value  # type: ignore[assignment]
            execution.error_message = "Cancellation completed by cleanup job"
            execution.completed_at = now
        except Exception as e:
            logger.error(f"Failed to cleanup cancelling execution {execution.id}: {e}")
            failed_count += 1

    await ctx.db.flush()

    total_cleaned = len(pending_executions) + len(running_executions) + len(cancelling_executions) - failed_count

    logger.info(
        f"Cleanup triggered: {total_cleaned} executions cleaned "
        f"({len(pending_executions)} pending, {len(running_executions)} running, "
        f"{len(cancelling_executions)} cancelling, {failed_count} failed)"
    )

    return CleanupTriggeredResponse(
        cleaned=total_cleaned,
        pending=len(pending_executions),
        running=len(running_executions),
        failed=failed_count,
    )


@router.post(
    "/cleanup/redis-orphans",
    summary="Cleanup orphaned Redis pending executions",
    description="Clean up Redis pending executions that are too old (Platform admin only)",
    dependencies=[RequirePlatformAdmin],
)
async def cleanup_redis_orphans(
    ctx: Context,
    minutes: int = Query(10, description="Minutes since creation to consider orphaned"),
) -> dict:
    """
    Clean up orphaned Redis pending executions.

    With Redis-first architecture, pending executions are stored in Redis until
    the worker picks them up. If the worker fails to pick up an execution within
    a reasonable time (default 10 minutes), we mark it as failed.

    This endpoint:
    1. Scans Redis for pending executions older than `minutes`
    2. Checks if they exist in PostgreSQL (worker should have created record)
    3. If not in PostgreSQL, creates a FAILED record
    4. Deletes the Redis pending entry
    """
    # For now, Redis pending entries have 1 hour TTL and will auto-expire
    # This endpoint is a placeholder for more sophisticated cleanup
    # A full implementation would:
    # 1. SCAN Redis for keys matching bifrost:exec:*:pending
    # 2. Check each key's created_at timestamp
    # 3. If too old, create FAILED PostgreSQL record and delete Redis entry

    # For Redis-first architecture, the worker should pick up jobs within seconds
    # Redis TTL (1 hour) provides automatic cleanup for truly orphaned entries
    # Manual cleanup via this endpoint is optional

    logger.info(f"Redis orphan cleanup triggered (threshold: {log_safe(minutes)} minutes)")

    return {
        "message": "Redis pending entries auto-expire via TTL. Use /cleanup/trigger for PostgreSQL cleanup.",
        "redis_ttl_hours": 1,
        "threshold_minutes": minutes,
    }
