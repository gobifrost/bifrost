"""
Queue position tracking for workflow executions.

Uses a Redis sorted set to track pending executions and provide
queue position visibility. Updates are event-driven - positions
are recalculated and published when the queue changes.
"""

import json
import logging
import time

import redis.asyncio as aioredis

from src.config import get_settings

logger = logging.getLogger(__name__)

# Redis key for the queue sorted set
QUEUE_KEY = "bifrost:queue:pending"


async def _get_redis() -> aioredis.Redis:
    """Create a Redis client scoped to the caller's event loop."""
    settings = get_settings()
    return aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
    )


async def _close_redis(r: aioredis.Redis) -> None:
    close = getattr(r, "aclose", None)
    if close is not None:
        await close()


async def add_to_queue(execution_id: str) -> int:
    """
    Add execution to queue tracking and publish position updates.

    Args:
        execution_id: Unique execution ID

    Returns:
        Position in queue (1-based)
    """
    r = await _get_redis()
    try:
        timestamp = time.time()

        # Add to sorted set with timestamp as score
        await r.zadd(QUEUE_KEY, {execution_id: timestamp})

        # Get position (0-based rank + 1 for 1-based position)
        rank = await r.zrank(QUEUE_KEY, execution_id)
        position = (rank + 1) if rank is not None else 1
    finally:
        await _close_redis(r)

    logger.debug(f"Added execution {execution_id} to queue at position {position}")

    # Publish updated positions to all queued executions
    await publish_all_queue_positions()

    return position


async def remove_from_queue(execution_id: str) -> None:
    """
    Remove execution from queue tracking and publish position updates.

    Called when execution starts running or is cancelled before starting.

    Args:
        execution_id: Execution ID to remove
    """
    r = await _get_redis()
    try:
        # Remove from sorted set
        removed = await r.zrem(QUEUE_KEY, execution_id)
    finally:
        await _close_redis(r)

    if removed:
        logger.debug(f"Removed execution {execution_id} from queue")
        # Publish updated positions to remaining queued executions
        await publish_all_queue_positions()


async def get_queue_position(execution_id: str) -> int | None:
    """
    Get current queue position for an execution.

    Args:
        execution_id: Execution ID

    Returns:
        Position (1-based) or None if not in queue
    """
    r = await _get_redis()
    try:
        rank = await r.zrank(QUEUE_KEY, execution_id)
    finally:
        await _close_redis(r)

    if rank is not None:
        return rank + 1  # Convert 0-based to 1-based
    return None


async def get_queue_depth() -> int:
    """
    Get total number of executions in queue.

    Returns:
        Queue depth (number of pending executions)
    """
    r = await _get_redis()
    try:
        return await r.zcard(QUEUE_KEY)
    finally:
        await _close_redis(r)


async def get_all_queue_positions() -> list[tuple[str, int]]:
    """
    Get all execution IDs with their positions.

    Returns:
        List of (execution_id, position) tuples, ordered by position
    """
    r = await _get_redis()
    try:
        # Get all members in order (lowest score = earliest = position 1)
        members = await r.zrange(QUEUE_KEY, 0, -1)
    finally:
        await _close_redis(r)

    # Return as list of (execution_id, 1-based position) tuples
    return [(member, idx + 1) for idx, member in enumerate(members)]


async def publish_all_queue_positions() -> None:
    """
    Publish current queue position to all queued executions.

    Called after any queue mutation (add/remove) to update
    all waiting clients with their new positions.
    """
    from src.core.pubsub import publish_execution_update

    positions = await get_all_queue_positions()

    for exec_id, position in positions:
        try:
            await publish_execution_update(
                exec_id,
                "Pending",
                {
                    "queuePosition": position,
                    "waitReason": "queued",
                }
            )
        except Exception as e:
            # Don't let one failed publish stop others
            logger.warning(f"Failed to publish queue position for {exec_id}: {e}")


async def cleanup_stale_entries(max_age_seconds: int = 600) -> int:
    """
    Remove stale entries from queue (safety cleanup).

    Entries older than max_age_seconds are removed. This handles
    edge cases where executions were orphaned without proper cleanup.

    Args:
        max_age_seconds: Maximum age for queue entries (default 10 min)

    Returns:
        Number of entries removed
    """
    r = await _get_redis()
    try:
        cutoff = time.time() - max_age_seconds

        # Remove entries with score (timestamp) less than cutoff
        removed = await r.zremrangebyscore(QUEUE_KEY, "-inf", cutoff)
    finally:
        await _close_redis(r)

    if removed:
        logger.info(f"Cleaned up {removed} stale queue entries")
        await publish_all_queue_positions()

    return removed


async def get_all_pending_executions() -> list[dict]:
    """
    Get all pending executions with metadata for heartbeat reporting.

    Returns:
        List of dicts with execution_id, workflow_id, workflow_name,
        organization_name, and queued_at for each pending execution.
    """
    r = await _get_redis()
    try:
        # Get execution IDs from sorted set
        exec_ids = await r.zrange(QUEUE_KEY, 0, -1)

        items = []
        for exec_id in exec_ids:
            # Get execution context from Redis (stored when queued)
            context = await r.get(f"bifrost:exec:{exec_id}:context")
            if context:
                try:
                    data = json.loads(context)
                    items.append({
                        "execution_id": exec_id,
                        "workflow_id": data.get("workflow_id"),
                        "workflow_name": data.get("name"),
                        "organization_name": data.get("organization", {}).get("name")
                        if isinstance(data.get("organization"), dict)
                        else None,
                        "queued_at": data.get("queued_at"),
                    })
                except json.JSONDecodeError:
                    # Invalid context, include execution_id only
                    items.append({
                        "execution_id": exec_id,
                        "workflow_id": None,
                        "workflow_name": None,
                        "organization_name": None,
                        "queued_at": None,
                    })
            else:
                # No context found, include execution_id only
                items.append({
                    "execution_id": exec_id,
                    "workflow_id": None,
                    "workflow_name": None,
                    "organization_name": None,
                    "queued_at": None,
                })
    finally:
        await _close_redis(r)

    return items
