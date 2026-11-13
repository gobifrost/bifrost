"""Sample worker heartbeats from Redis into the worker_metrics table.

Runs every 60s in the scheduler container. Worker processes publish
heartbeats to Redis but cannot connect to the DB — this job bridges
the gap by reading heartbeats from Redis and persisting snapshots.
"""

import json
import logging

from src.core.database import get_session_factory
from src.core.redis_client import get_redis_client
from src.models.orm.worker_metric import WorkerMetric

logger = logging.getLogger(__name__)


async def sample_worker_metrics() -> dict:
    """
    Read latest heartbeats from Redis and persist to worker_metrics.

    Returns:
        Summary with workers_sampled count.
    """
    redis_client = get_redis_client()
    if not redis_client:
        return {"workers_sampled": 0, "error": "Redis unavailable"}

    try:
        # Find all registered worker pools in Redis
        pool_keys: list[str] = []
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(cursor, match="bifrost:pool:*:heartbeat", count=100)
            pool_keys.extend(keys)
            if cursor == 0:
                break

        if not pool_keys:
            return {"workers_sampled": 0}

        sampled = 0
        session_factory = get_session_factory()
        async with session_factory() as db:
            for key in pool_keys:
                try:
                    raw = await redis_client.get(key)
                    if not raw:
                        continue

                    heartbeat = json.loads(raw)
                    memory_current = heartbeat.get("memory_current_bytes", -1)
                    memory_max = heartbeat.get("memory_max_bytes", -1)

                    # Skip only if current reading is unavailable. An unknown
                    # max (no cgroup limit) is a legitimate state — persist
                    # NULL so the chart still has data points.
                    if memory_current < 0:
                        continue

                    metric = WorkerMetric(
                        worker_id=heartbeat.get("worker_id", "unknown"),
                        memory_current=memory_current,
                        memory_max=memory_max if memory_max > 0 else None,
                        fork_count=heartbeat.get("pool_size", 0),
                        busy_count=heartbeat.get("busy_count", 0),
                        idle_count=heartbeat.get("idle_count", 0),
                    )
                    db.add(metric)
                    sampled += 1
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Skipping malformed heartbeat from {key}: {e}")

            if sampled > 0:
                await db.commit()

        logger.debug(f"Worker metrics sampled: {sampled} workers from {len(pool_keys)} heartbeats")
        return {"workers_sampled": sampled}

    except Exception as e:
        logger.exception(f"Worker metrics sampling failed: {e}", exc_info=True)
        return {"workers_sampled": 0, "error": str(e)}
