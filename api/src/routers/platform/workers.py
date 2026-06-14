"""
Platform Admin Workers Router

API endpoints for managing process pools, viewing queue status, and handling
stuck executions. All endpoints require platform admin privileges.

The on-demand model uses ProcessPoolManager with one-shot workers, which
only ever report two states in their heartbeat:
- BUSY: Process currently executing its single workload
- KILLED: Process was terminated (pending removal)
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import CurrentSuperuser, get_current_superuser
from src.core.database import get_db
from src.core.log_safety import log_safe
from src.models import Execution, Workflow
from src.models.contracts.platform import (
    PoolDetail,
    PoolsListResponse,
    PoolStatsResponse,
    PoolSummary,
    ProcessInfo,
    QueueItem,
    QueueStatusResponse,
    RecycleAllRequest,
    RecycleAllResponse,
    RecycleProcessRequest,
    RecycleProcessResponse,
    StuckHistoryResponse,
    StuckWorkflowStats,
    WorkerMetricPoint,
    WorkerMetricsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/platform/workers",
    tags=["Platform Admin - Workers"],
    dependencies=[Depends(get_current_superuser)],
)


_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    """Get Redis client for worker data."""
    global _redis
    if _redis is None:
        from src.config import get_settings
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=5.0,
        )
    return _redis


# =============================================================================
# Static routes MUST be defined before dynamic routes (/{worker_id})
# =============================================================================


@router.get(
    "/metrics",
    response_model=WorkerMetricsResponse,
    summary="Get worker metrics time-series",
    description="Returns time-series memory data for the aggregate chart. "
    "Downsampled for longer ranges.",
)
async def get_worker_metrics(
    _admin: CurrentSuperuser,
    db: AsyncSession = Depends(get_db),
    range: str = Query(
        default="1h",
        pattern=r"^(1h|6h|24h|7d)$",
        description="Time range: 1h, 6h, 24h, 7d",
    ),
) -> WorkerMetricsResponse:
    """Get worker metrics for the diagnostics memory chart."""
    from datetime import timedelta

    from sqlalchemy import func, select, text

    from src.models.orm.worker_metric import WorkerMetric

    # Parse range
    range_map = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
    }
    delta = range_map[range]
    cutoff = datetime.now(timezone.utc) - delta

    # Bucket sizes: 1h=1min, 6h=5min, 24h=1hr, 7d=30min
    bucket_interval = {"1h": "1 minute", "6h": "5 minutes", "24h": "1 hour", "7d": "30 minutes"}[range]
    epoch = datetime(2000, 1, 1, tzinfo=timezone.utc)

    # Format group labels: 1h/6h/24h → "HH:MM", 7d → "Mon D"
    group_formats = {"1h": "%H:%M", "6h": "%H:%M", "24h": "%H:%M", "7d": "%b %-d"}
    group_fmt = group_formats[range]

    bucket_ts = func.date_bin(
        text(f"'{bucket_interval}'::interval"),
        WorkerMetric.timestamp,
        epoch,
    ).label("bucket_ts")

    stmt = (
        select(
            bucket_ts,
            WorkerMetric.worker_id,
            func.avg(WorkerMetric.memory_current).label("avg_memory_current"),
            func.max(WorkerMetric.memory_max).label("memory_max"),
            func.round(func.avg(WorkerMetric.fork_count)).label("avg_fork_count"),
            func.round(func.avg(WorkerMetric.busy_count)).label("avg_busy_count"),
            func.round(func.avg(WorkerMetric.idle_count)).label("avg_idle_count"),
        )
        .where(WorkerMetric.timestamp >= cutoff)
        .group_by(bucket_ts, WorkerMetric.worker_id)
        .order_by(bucket_ts)
    )
    result = await db.execute(stmt)
    rows = result.all()

    points = [
        WorkerMetricPoint(
            group=row.bucket_ts.strftime(group_fmt),
            worker_id=row.worker_id,
            memory_current=int(row.avg_memory_current),
            memory_max=int(row.memory_max) if row.memory_max is not None else -1,
            fork_count=int(row.avg_fork_count),
            busy_count=int(row.avg_busy_count),
            idle_count=int(row.avg_idle_count),
        )
        for row in rows
    ]

    return WorkerMetricsResponse(range=range, points=points)


@router.get(
    "/stats",
    response_model=PoolStatsResponse,
    summary="Get pool statistics",
    description="Get aggregated statistics across all process pools",
)
async def get_pool_stats(
    _admin: CurrentSuperuser,
) -> PoolStatsResponse:
    """
    Get aggregated pool statistics.

    Returns counts of total pools, processes, idle, and busy across
    all registered pools.
    """
    r = await _get_redis()

    # Find all pool registration keys
    cursor = 0
    pool_keys: list[str] = []

    while True:
        cursor, keys = await r.scan(cursor, match="bifrost:pool:*", count=100)
        # Exclude heartbeat keys - only count pool registration keys
        for key in keys:
            if ":heartbeat" not in key and ":commands" not in key:
                pool_keys.append(key)
        if cursor == 0:
            break

    total_pools = len(pool_keys)
    total_processes = 0
    total_idle = 0
    total_busy = 0

    for key in pool_keys:
        parts = key.split(":")
        if len(parts) != 3:
            continue
        worker_id = parts[2]

        # Get heartbeat for process counts
        heartbeat_key = f"bifrost:pool:{worker_id}:heartbeat"
        heartbeat_data = await r.get(heartbeat_key)

        if heartbeat_data:
            try:
                hb = json.loads(heartbeat_data)
                total_processes += hb.get("pool_size", 0)
                total_idle += hb.get("idle_count", 0)
                total_busy += hb.get("busy_count", 0)
            except json.JSONDecodeError as e:
                # Corrupted heartbeat JSON for this worker — skip its contribution
                logger.debug(f"invalid heartbeat JSON for worker {log_safe(worker_id)}: {log_safe(e)}")

    return PoolStatsResponse(
        total_pools=total_pools,
        total_processes=total_processes,
        total_idle=total_idle,
        total_busy=total_busy,
    )


# =============================================================================
# Dynamic routes with path parameters
# =============================================================================


@router.get(
    "",
    response_model=PoolsListResponse,
    summary="List all process pools",
    description="Get a list of all registered process pools and their current status",
)
async def list_pools(
    _admin: CurrentSuperuser,
) -> PoolsListResponse:
    """
    List all registered process pools from Redis.

    Returns pools with their current state including pool size,
    idle/busy counts, and last heartbeat time.
    """
    r = await _get_redis()

    # Find all pool registration keys
    # Pools register with pattern: bifrost:pool:{worker_id}
    cursor = 0
    pool_keys: list[str] = []

    while True:
        cursor, keys = await r.scan(cursor, match="bifrost:pool:*", count=100)
        pool_keys.extend(keys)
        if cursor == 0:
            break

    pools: list[PoolSummary] = []

    for key in pool_keys:
        # Extract worker_id from key (bifrost:pool:{worker_id})
        parts = key.split(":")
        if len(parts) != 3:
            continue
        worker_id = parts[2]

        # Get pool registration data
        data: dict[str, str] = await r.hgetall(key)  # type: ignore[assignment]

        # Get latest heartbeat from pubsub channel storage
        heartbeat_key = f"bifrost:pool:{worker_id}:heartbeat"
        heartbeat_data = await r.get(heartbeat_key)

        pool_info = PoolSummary(
            worker_id=worker_id,
            hostname=data.get("hostname"),
            status=data.get("status"),
            started_at=data.get("started_at"),
        )

        if heartbeat_data:
            try:
                hb = json.loads(heartbeat_data)
                pool_info.pool_size = hb.get("pool_size", 0)
                pool_info.idle_count = hb.get("idle_count", 0)
                pool_info.busy_count = hb.get("busy_count", 0)
                pool_info.last_heartbeat = hb.get("timestamp")
                pool_info.requirements_installed = hb.get("requirements_installed")
                pool_info.requirements_total = hb.get("requirements_total")
                pool_info.memory_current_bytes = hb.get("memory_current_bytes")
                pool_info.memory_max_bytes = hb.get("memory_max_bytes")
            except json.JSONDecodeError:
                logger.warning(f"Invalid heartbeat JSON for pool {log_safe(worker_id)}")

        pools.append(pool_info)

    return PoolsListResponse(pools=pools, total=len(pools))


@router.get(
    "/{worker_id}",
    response_model=PoolDetail,
    summary="Get pool details",
    description="Get detailed information about a specific process pool including all processes",
)
async def get_pool(
    worker_id: str,
    _admin: CurrentSuperuser,
) -> PoolDetail:
    """
    Get detailed pool information.

    Returns the latest heartbeat data for the pool including
    all processes and their current state.
    """
    r = await _get_redis()

    # Check pool exists
    pool_key = f"bifrost:pool:{worker_id}"
    exists = await r.exists(pool_key)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool {worker_id} not found",
        )

    # Get registration data
    data: dict[str, str] = await r.hgetall(pool_key)  # type: ignore[assignment]

    # Get latest heartbeat
    heartbeat_key = f"bifrost:pool:{worker_id}:heartbeat"
    heartbeat_data = await r.get(heartbeat_key)

    result = PoolDetail(
        worker_id=worker_id,
        hostname=data.get("hostname"),
        status=data.get("status"),
        started_at=data.get("started_at"),
    )

    if heartbeat_data:
        try:
            hb = json.loads(heartbeat_data)
            result.last_heartbeat = hb.get("timestamp")

            # Parse process info from heartbeat
            for p in hb.get("processes", []):
                # Get execution info if busy
                execution_info = p.get("execution")
                current_execution_id = None
                if execution_info:
                    current_execution_id = execution_info.get("execution_id")

                result.processes.append(
                    ProcessInfo(
                        process_id=p.get("process_id", "unknown"),
                        pid=p.get("pid", 0),
                        state=p.get("state", "idle"),
                        current_execution_id=current_execution_id,
                        executions_completed=p.get("executions_completed", 0),
                        uptime_seconds=p.get("uptime_seconds", 0),
                        memory_mb=p.get("memory_mb", 0),
                        is_alive=True,  # If in heartbeat, it's alive
                    )
                )
        except json.JSONDecodeError:
            logger.warning(f"Invalid heartbeat JSON for pool {log_safe(worker_id)}")

    return result


@router.post(
    "/{worker_id}/processes/{pid}/recycle",
    response_model=RecycleProcessResponse,
    summary="Recycle a process",
    description="Trigger manual recycle of a process in the pool via Redis pub/sub",
)
async def recycle_process(
    worker_id: str,
    pid: int,
    admin: CurrentSuperuser,
    request: RecycleProcessRequest | None = None,
) -> RecycleProcessResponse:
    """
    Trigger manual recycle of a process in a pool.

    For idle processes, the process is terminated immediately and
    a new one is spawned to replace it.

    For busy processes, this request is rejected - you must wait for
    the execution to complete or let it time out.

    The recycle command is published via Redis pub/sub and processed
    asynchronously by the pool manager.
    """
    r = await _get_redis()

    # Check pool exists
    pool_key = f"bifrost:pool:{worker_id}"
    exists = await r.exists(pool_key)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool {worker_id} not found",
        )

    # Publish recycle command via Redis pub/sub
    command_channel = f"bifrost:pool:{worker_id}:commands"
    command = {
        "action": "recycle_process",
        "pid": pid,
        "reason": request.reason if request else "manual_recycle",
        "requested_by": str(admin.user_id),
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }

    await r.publish(command_channel, json.dumps(command))

    logger.info(
        f"Published recycle command for pool {log_safe(worker_id)} process PID={log_safe(pid)} "
        f"by user {admin.user_id}"
    )

    return RecycleProcessResponse(
        success=True,
        message=f"Recycle request sent for process PID={pid}",
        worker_id=worker_id,
        pid=pid,
    )


@router.post(
    "/{worker_id}/recycle-all",
    response_model=RecycleAllResponse,
    summary="Recycle all processes",
    description="Mark all processes in a pool for graceful recycling",
)
async def recycle_all_processes(
    worker_id: str,
    admin: CurrentSuperuser,
    request: RecycleAllRequest | None = None,
) -> RecycleAllResponse:
    """
    Trigger recycle of all processes in a pool.

    Idle processes are recycled immediately.
    Busy processes are marked for recycling after their current execution completes.

    This is useful for:
    - Memory cleanup after long-running operations
    - Picking up newly installed packages
    - Refreshing interpreter state
    """
    r = await _get_redis()

    # Check pool exists
    pool_key = f"bifrost:pool:{worker_id}"
    exists = await r.exists(pool_key)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool {worker_id} not found",
        )

    # Get current pool size from heartbeat
    heartbeat_key = f"bifrost:pool:{worker_id}:heartbeat"
    heartbeat_data = await r.get(heartbeat_key)
    processes_affected = 0

    if heartbeat_data:
        try:
            hb = json.loads(heartbeat_data)
            processes_affected = hb.get("pool_size", 0)
        except json.JSONDecodeError as e:
            # Corrupted heartbeat JSON — processes_affected stays 0, recycle proceeds
            logger.debug(f"invalid heartbeat JSON for worker {log_safe(worker_id)}: {log_safe(e)}")

    # Publish recycle_all command via Redis pub/sub
    command_channel = f"bifrost:pool:{worker_id}:commands"
    command = {
        "action": "recycle_all",
        "reason": request.reason if request else "Manual recycle from Diagnostics UI",
        "requested_by": str(admin.user_id),
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }

    await r.publish(command_channel, json.dumps(command))

    logger.info(
        f"Published recycle_all command for pool {log_safe(worker_id)} "
        f"({processes_affected} processes) by user {admin.user_id}"
    )

    return RecycleAllResponse(
        success=True,
        message=f"Recycle request sent for all {processes_affected} processes",
        worker_id=worker_id,
        processes_affected=processes_affected,
    )


# =============================================================================
# Queue Status Endpoints
# =============================================================================


queue_router = APIRouter(
    prefix="/api/platform/queue",
    tags=["Platform Admin - Queue"],
    dependencies=[Depends(get_current_superuser)],
)


@queue_router.get(
    "",
    response_model=QueueStatusResponse,
    summary="Get queue status",
    description="Get pending executions in the queue",
)
async def get_queue(
    _admin: CurrentSuperuser,
    limit: int = Query(50, ge=1, le=500, description="Maximum number of items"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
) -> QueueStatusResponse:
    """
    Get pending executions in the queue.

    Returns executions waiting to be picked up by a worker,
    ordered by queue position.
    """
    from src.services.execution.queue_tracker import get_all_queue_positions

    all_positions = await get_all_queue_positions()
    total = len(all_positions)

    # Apply pagination
    paginated = all_positions[offset : offset + limit]

    items = [
        QueueItem(
            execution_id=exec_id,
            position=position,
        )
        for exec_id, position in paginated
    ]

    return QueueStatusResponse(total=total, items=items)


# =============================================================================
# Stuck History Endpoints
# =============================================================================


stuck_router = APIRouter(
    prefix="/api/platform/stuck-history",
    tags=["Platform Admin - Stuck History"],
    dependencies=[Depends(get_current_superuser)],
)


@stuck_router.get(
    "",
    response_model=StuckHistoryResponse,
    summary="Get stuck execution history",
    description="Get aggregated stuck workflow statistics",
)
async def get_stuck_history(
    _admin: CurrentSuperuser,
    hours: int = Query(24, ge=1, le=720, description="Time window in hours"),
    db: AsyncSession = Depends(get_db),
) -> StuckHistoryResponse:
    """
    Get aggregated stuck workflow statistics.

    Returns workflows that have had stuck executions in the time window,
    grouped by workflow with count and last occurrence.

    Note: Stuck executions are identified by error_message containing
    'stuck' or 'timeout' patterns, as there's no dedicated error_type field.
    """
    # Use naive datetime since the database stores naive datetimes
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Query executions with stuck-like errors
    # Since there's no error_type field, we match on error_message patterns
    query = (
        select(
            Execution.workflow_name,
            func.count(Execution.id).label("stuck_count"),
            func.max(Execution.created_at).label("last_stuck_at"),
        )
        .where(
            Execution.created_at >= cutoff,
            Execution.error_message.ilike("%stuck%")
            | Execution.error_message.ilike("%timeout%")
            | Execution.error_message.ilike("%timed out%"),
        )
        .group_by(Execution.workflow_name)
        .order_by(desc("stuck_count"))
    )

    result = await db.execute(query)
    rows = result.all()

    # Look up workflow IDs from names
    workflow_stats: list[StuckWorkflowStats] = []

    for row in rows:
        # Try to find the workflow by name
        wf_query = select(Workflow.id).where(Workflow.name == row.workflow_name).limit(1)
        wf_result = await db.execute(wf_query)
        wf_row = wf_result.scalar_one_or_none()

        workflow_stats.append(
            StuckWorkflowStats(
                workflow_id=str(wf_row) if wf_row else "unknown",
                workflow_name=row.workflow_name,
                stuck_count=row.stuck_count,
                last_stuck_at=row.last_stuck_at,
            )
        )

    return StuckHistoryResponse(hours=hours, workflows=workflow_stats)
