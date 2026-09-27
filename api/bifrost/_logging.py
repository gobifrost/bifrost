"""
Unified log streaming for Bifrost SDK.

Writes execution logs to Redis Stream as the single source of truth.
Two consumers read from this stream:
1. PubSub forwarder - Publishes to WebSocket for real-time delivery
2. Log persistence worker - Batch inserts to Postgres

This replaces the previous dual-write pattern (sync Postgres + Redis PubSub)
which caused duplicate logs and event loop affinity issues.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from .models import ExecutionLog
from uuid import UUID

import redis as redis_sync

from src.core.cache.keys import execution_logs_stream_key
from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)

# Thread-local storage for Redis connections and sequence counters
_local = threading.local()

# Per-execution sequence counters for log ordering.
# Thread-local counters for sync callers, dict for async callers.
_async_sequence_counters: dict[str, int] = {}


def clear_sequence_counter(execution_id: str) -> None:
    """Remove sequence counter for a completed execution.

    Called from simple_worker.py after each execution to prevent
    unbounded growth of the module-level counter dict.
    """
    _async_sequence_counters.pop(str(execution_id), None)


def _serialize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Ensure metadata is JSON-serializable by converting datetime to ISO strings.

    This prevents "Object of type datetime is not JSON serializable" errors
    when metadata contains datetime objects (e.g., from workflow code).
    """
    if metadata is None:
        return None

    def convert(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert(item) for item in obj]
        return obj

    return convert(metadata)


def _get_sync_redis() -> redis_sync.Redis:
    """Get thread-local sync Redis connection."""
    if not hasattr(_local, "redis") or _local.redis is None:
        from src.config import get_settings

        settings = get_settings()
        _local.redis = redis_sync.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _local.redis


def append_log_to_stream(
    execution_id: str | UUID,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> str | None:
    """
    Append a log entry to the execution's Redis Stream.

    This is the single source of truth for execution logs.
    Call this from workflow threads (sync context).

    Args:
        execution_id: Execution UUID
        level: Log level (INFO, WARNING, ERROR, DEBUG, CRITICAL)
        message: Log message text
        metadata: Optional JSON metadata
        timestamp: Optional timestamp (defaults to now)

    Returns:
        Stream entry ID if successful, None on error
    """
    exec_id = str(execution_id)
    ts = timestamp or datetime.now(timezone.utc)
    stream_key = execution_logs_stream_key(exec_id)

    safe_metadata = _serialize_metadata(metadata)
    entry = {
        "execution_id": exec_id,
        "level": level.upper(),
        "message": message,
        "metadata": json.dumps(safe_metadata) if safe_metadata else "{}",
        "timestamp": ts.isoformat(),
    }

    try:
        r = _get_sync_redis()
        # XADD with automatic ID generation (*)
        # MAXLEN ~ 10000 keeps stream bounded (approximate trimming)
        entry_id: str = r.xadd(stream_key, entry, maxlen=10000)  # type: ignore[misc]
        return entry_id
    except Exception as e:
        logger.warning(f"Failed to append log to stream: {e}")
        # Reset connection on error
        _local.redis = None
        return None


def publish_log_to_pubsub(
    execution_id: str | UUID,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
    sequence: int | None = None,
) -> None:
    """
    Publish log entry directly to PubSub for immediate WebSocket delivery.

    This is called alongside append_log_to_stream for real-time streaming.
    The stream is the source of truth; PubSub is for immediate delivery.

    Args:
        execution_id: Execution UUID
        level: Log level
        message: Log message
        metadata: Optional metadata
        timestamp: Optional timestamp
        sequence: Monotonic sequence number for client-side ordering
    """
    exec_id = str(execution_id)
    ts = timestamp or datetime.now(timezone.utc)

    safe_metadata = _serialize_metadata(metadata)
    log_entry: dict[str, Any] = {
        "type": "execution_log",
        "executionId": exec_id,
        "level": level.upper(),
        "message": message,
        "metadata": safe_metadata,
        "timestamp": ts.isoformat(),
    }
    if sequence is not None:
        log_entry["sequence"] = sequence

    try:
        r = _get_sync_redis()
        r.publish(f"bifrost:execution:{exec_id}", json.dumps(log_entry))
    except Exception as e:
        logger.warning(f"Failed to publish log to PubSub: {e}")
        _local.redis = None


def log_and_broadcast(
    execution_id: str | UUID,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> str | None:
    """
    Write log to stream AND publish to PubSub in one call.

    This is the main entry point for logging from workflow threads.
    Replaces the old pattern of calling append_log_sync + broadcaster separately.

    Args:
        execution_id: Execution UUID
        level: Log level
        message: Log message
        metadata: Optional metadata
        timestamp: Optional timestamp

    Returns:
        Stream entry ID if successful, None on error
    """
    ts = timestamp or datetime.now(timezone.utc)

    # Increment thread-local sequence counter for this execution
    exec_id = str(execution_id)
    if not hasattr(_local, "sequence_counters"):
        _local.sequence_counters = {}
    seq = _local.sequence_counters.get(exec_id, 0)
    _local.sequence_counters[exec_id] = seq + 1

    # Write to stream (source of truth for persistence)
    entry_id = append_log_to_stream(
        execution_id=execution_id,
        level=level,
        message=message,
        metadata=metadata,
        timestamp=ts,
    )

    # Publish to PubSub (immediate WebSocket delivery)
    publish_log_to_pubsub(
        execution_id=execution_id,
        level=level,
        message=message,
        metadata=metadata,
        timestamp=ts,
        sequence=seq,
    )

    return entry_id


# =============================================================================
# Service logs: attempt-scoped streams for supervised @service executions
# =============================================================================


def publish_service_log(
    service_id: str,
    attempt_id: str,
    level: str,
    message: str,
    timestamp: datetime | None = None,
) -> str | None:
    """
    Append a service log line to the attempt's Redis Stream and publish it
    for live delivery.

    Attempt-scoped by construction (``bifrost:service-logs:{attempt_id}``,
    bounded ``MAXLEN ~10000`` like execution streams): a stale fenced attempt
    keeps appending to its own superseded stream, which no reader follows
    after reassignment. Postgres persistence and the service-wide timeline
    arrive in Slice 4; this is the continuous bounded flush substrate.

    Args:
        service_id: Parent service definition UUID (fan-out correlation)
        attempt_id: Owning attempt UUID (stream scope)
        level: Log level (INFO, WARNING, ERROR, DEBUG, CRITICAL)
        message: Log message text (caller scrubs secrets first)
        timestamp: Optional timestamp (defaults to now)

    Returns:
        Stream entry ID if successful, None on error
    """
    from src.core.cache.keys import service_logs_stream_key

    ts = timestamp or datetime.now(timezone.utc)
    stream_key = service_logs_stream_key(str(attempt_id))
    entry = {
        "service_id": str(service_id),
        "attempt_id": str(attempt_id),
        "level": level.upper(),
        "message": message,
        "timestamp": ts.isoformat(),
    }

    try:
        r = _get_sync_redis()
        entry_id: str = r.xadd(stream_key, entry, maxlen=10000)  # type: ignore[misc]
    except Exception as e:
        logger.warning(f"Failed to append service log to stream: {e}")
        _local.redis = None
        return None

    try:
        r = _get_sync_redis()
        r.publish(
            stream_key,
            json.dumps({"type": "service_log", **entry}),
        )
    except Exception as e:
        logger.warning(f"Failed to publish service log to PubSub: {e}")
        _local.redis = None

    return entry_id


# =============================================================================
# Async versions for use in async contexts (API routes, consumers)
# =============================================================================


async def append_log_to_stream_async(
    execution_id: str | UUID,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> str | None:
    """
    Async version of append_log_to_stream.

    Use this in async contexts (API routes, background tasks).
    """
    from src.core.cache import get_redis

    exec_id = str(execution_id)
    ts = timestamp or datetime.now(timezone.utc)
    stream_key = execution_logs_stream_key(exec_id)

    safe_metadata = _serialize_metadata(metadata)
    entry = {
        "execution_id": exec_id,
        "level": level.upper(),
        "message": message,
        "metadata": json.dumps(safe_metadata) if safe_metadata else "{}",
        "timestamp": ts.isoformat(),
    }

    try:
        async with get_redis() as r:
            entry_id = await r.xadd(stream_key, entry, maxlen=10000)  # type: ignore[misc]
            return entry_id
    except Exception as e:
        logger.warning(f"Failed to append log to stream (async): {e}")
        return None


async def read_logs_from_stream(
    execution_id: str | UUID,
    start: str = "0",
    count: int = 100,
) -> "list[ExecutionLog]":
    """
    Read log entries from an execution's Redis Stream.

    Args:
        execution_id: Execution UUID
        start: Stream ID to start from (default: beginning)
        count: Maximum entries to read

    Returns:
        List of ExecutionLog entries
    """
    from src.core.cache import get_redis
    from .models import ExecutionLog

    exec_id = str(execution_id)
    stream_key = execution_logs_stream_key(exec_id)

    try:
        async with get_redis() as r:
            # XRANGE returns [(id, {field: value}), ...]
            entries = await r.xrange(stream_key, min=start, count=count)  # type: ignore[misc]

            logs = []
            for entry_id, data in entries:
                logs.append(ExecutionLog(
                    id=entry_id,
                    execution_id=data.get("execution_id", exec_id),
                    level=data.get("level", "INFO"),
                    message=data.get("message", ""),
                    metadata=json.loads(data.get("metadata", "{}")),
                    timestamp=data.get("timestamp", ""),
                ))
            return logs
    except Exception as e:
        logger.warning(f"Failed to read logs from stream: {e}")
        return []


async def publish_log_to_pubsub_async(
    execution_id: str | UUID,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
    sequence: int | None = None,
) -> None:
    """
    Async version of publish_log_to_pubsub.

    Publish log entry directly to PubSub for immediate WebSocket delivery.
    Use this in async contexts (API routes, background tasks).

    Args:
        execution_id: Execution UUID
        level: Log level
        message: Log message
        metadata: Optional metadata
        timestamp: Optional timestamp
        sequence: Monotonic sequence number for client-side ordering
    """
    from src.core.cache import get_redis

    exec_id = str(execution_id)
    ts = timestamp or datetime.now(timezone.utc)

    safe_metadata = _serialize_metadata(metadata)
    log_entry: dict[str, Any] = {
        "type": "execution_log",
        "executionId": exec_id,
        "level": level.upper(),
        "message": message,
        "metadata": safe_metadata,
        "timestamp": ts.isoformat(),
    }
    if sequence is not None:
        log_entry["sequence"] = sequence

    try:
        async with get_redis() as r:
            await r.publish(f"bifrost:execution:{exec_id}", json.dumps(log_entry))
    except Exception as e:
        logger.warning(f"Failed to publish log to PubSub (async): {e}")


async def log_and_broadcast_async(
    execution_id: str | UUID,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> str | None:
    """
    Async version of log_and_broadcast.

    Write log to stream AND publish to PubSub in one call.
    Use this in async contexts (API routes) to match workflow engine behavior.

    Args:
        execution_id: Execution UUID
        level: Log level
        message: Log message
        metadata: Optional metadata
        timestamp: Optional timestamp

    Returns:
        Stream entry ID if successful, None on error
    """
    ts = timestamp or datetime.now(timezone.utc)

    # Increment async sequence counter for this execution
    exec_id = str(execution_id)
    seq = _async_sequence_counters.get(exec_id, 0)
    _async_sequence_counters[exec_id] = seq + 1

    # Write to stream (source of truth for persistence)
    entry_id = await append_log_to_stream_async(
        execution_id=execution_id,
        level=level,
        message=message,
        metadata=metadata,
        timestamp=ts,
    )

    # Publish to PubSub (immediate WebSocket delivery)
    await publish_log_to_pubsub_async(
        execution_id=execution_id,
        level=level,
        message=message,
        metadata=metadata,
        timestamp=ts,
        sequence=seq,
    )

    return entry_id


def close_thread_redis() -> None:
    """Close the thread-local Redis connection. Call on thread cleanup."""
    if hasattr(_local, "redis") and _local.redis is not None:
        try:
            _local.redis.close()
        except Exception as e:
            # Best-effort cleanup on thread teardown — connection may already be invalid
            logger.debug(f"close_thread_redis ignoring close error: {e}")
        _local.redis = None
    if hasattr(_local, "sequence_counters"):
        _local.sequence_counters = {}


# =============================================================================
# Log Persistence (flush from Redis Stream to Postgres)
# =============================================================================


async def flush_logs_to_postgres(
    execution_id: str | UUID,
    session: "AsyncSession | None" = None,
) -> int:
    """
    Flush all logs from Redis Stream to Postgres.

    Called at the end of execution to persist logs from the stream
    to the database. Uses batch inserts for efficiency.

    Args:
        execution_id: Execution UUID
        session: Optional database session. If provided, uses it and
                 caller is responsible for commit. If None, creates own session.

    Returns:
        Number of logs persisted
    """
    from src.models.orm import ExecutionLog

    exec_id = str(execution_id)
    exec_uuid = UUID(exec_id)
    stream_key = execution_logs_stream_key(exec_id)

    try:
        from src.core.cache import get_redis

        async with get_redis() as r:
            # Read all entries from stream
            entries = await r.xrange(stream_key, min="-", max="+")  # type: ignore[misc]

            if not entries:
                return 0

            # Parse entries - enumerate to preserve insertion order
            logs_to_insert = []
            for seq, (entry_id, data) in enumerate(entries):
                try:
                    # Parse timestamp and strip timezone (DB uses TIMESTAMP WITHOUT TIME ZONE)
                    ts_str = data.get("timestamp", datetime.now(timezone.utc).isoformat())
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is not None:
                        ts = ts.replace(tzinfo=None)

                    log_entry = ExecutionLog(
                        execution_id=exec_uuid,
                        level=data.get("level", "INFO"),
                        message=data.get("message", ""),
                        log_metadata=json.loads(data.get("metadata", "{}")),
                        timestamp=ts,
                        sequence=seq,
                    )
                    logs_to_insert.append(log_entry)
                except Exception as e:
                    logger.warning(f"Failed to parse log entry {entry_id}: {e}")
                    continue

            if not logs_to_insert:
                return 0

            # Batch insert to Postgres
            if session is not None:
                # Use provided session (caller manages commit)
                session.add_all(logs_to_insert)
            else:
                # Create own session
                from src.core.database import get_session_factory

                session_factory = get_session_factory()
                async with session_factory() as db:
                    db.add_all(logs_to_insert)
                    await db.commit()

            # Clear the stream after successful persistence
            await r.delete(stream_key)

            logger.debug(f"Flushed {len(logs_to_insert)} logs to Postgres for {log_safe(exec_id)}")
            return len(logs_to_insert)

    except Exception as e:
        logger.error(f"Failed to flush logs to Postgres: {e}")
        return 0
