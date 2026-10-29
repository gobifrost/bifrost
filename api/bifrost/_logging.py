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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from .models import ExecutionLog
from uuid import UUID

import redis as redis_sync

from src.core.cache.keys import execution_log_recovery_cursor_key, execution_logs_stream_key
from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)


def _reject_non_finite_json(value: str) -> None:
    """Reject JSON constants PostgreSQL JSONB cannot represent."""
    raise ValueError(f"non-finite JSON value: {value}")


@dataclass(frozen=True)
class LogFlushReceipt:
    """The exact Redis entries staged for one database transaction.

    A receipt is acknowledged only after the transaction that staged its rows
    commits. A process loss between those two steps intentionally permits a
    replay; execution-log delivery is at-least-once.
    """

    execution_id: str
    stream_key: str
    entry_ids: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.entry_ids)

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


def _require_server_process() -> None:
    from bifrost.client import get_engine_socket_path

    if get_engine_socket_path() is not None:
        raise RuntimeError("Direct log storage access is unavailable in execution children")


def _get_sync_redis() -> redis_sync.Redis:
    """Get thread-local sync Redis connection in the trusted server process."""
    _require_server_process()
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
    _require_server_process()
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
    _require_server_process()
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
    _require_server_process()
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


async def stage_logs_to_postgres(
    execution_id: str | UUID,
    session: "AsyncSession",
) -> LogFlushReceipt:
    """Stage parseable stream entries in ``session`` and return their receipt.

    This function never acknowledges Redis. Its caller must commit the session
    and then pass the returned receipt to :func:`acknowledge_staged_logs`.
    Malformed entries deliberately remain in the stream, while later valid
    entries continue to be staged in the same pass.
    """
    from sqlalchemy import func, select

    from src.core.cache import get_redis
    from src.models.orm import Execution, ExecutionLog

    _require_server_process()
    exec_id = str(execution_id)
    exec_uuid = UUID(exec_id)
    stream_key = execution_logs_stream_key(exec_id)

    async with get_redis() as redis:
        entries = await redis.xrange(stream_key, min="-", max="+")  # type: ignore[misc]
    if not entries:
        return LogFlushReceipt(exec_id, stream_key, ())

    parsed_entries: list[tuple[str, dict[str, Any], datetime]] = []
    for entry_id, data in entries:
        try:
            ts_str = data.get("timestamp", datetime.now(timezone.utc).isoformat())
            timestamp = datetime.fromisoformat(ts_str)
            if timestamp.tzinfo is not None:
                timestamp = timestamp.replace(tzinfo=None)
            parsed_entries.append((entry_id, data, timestamp))
        except Exception as exc:
            logger.warning("Failed to parse log entry %s: %s", entry_id, exc)
    if not parsed_entries:
        return LogFlushReceipt(exec_id, stream_key, ())

    # The row lock serializes sequence allocation with every other flusher.
    # Snapshotting before this lock is intentional: a competing flusher may
    # replay the same entry after a crash or race, but both rows remain ordered.
    execution = await session.scalar(
        select(Execution).where(Execution.id == exec_uuid).with_for_update()
    )
    if execution is None:
        return LogFlushReceipt(exec_id, stream_key, ())
    max_sequence = await session.scalar(
        select(func.coalesce(func.max(ExecutionLog.sequence), -1)).where(
            ExecutionLog.execution_id == exec_uuid
        )
    )
    next_sequence = (max_sequence if max_sequence is not None else -1) + 1
    logs_to_insert: list[ExecutionLog] = []
    entry_ids: list[str] = []
    for offset, (entry_id, data, timestamp) in enumerate(parsed_entries):
        try:
            level = data.get("level", "INFO")
            message = data.get("message", "")
            if not isinstance(level, str) or len(level) > 20:
                raise ValueError("level must be a string of at most 20 characters")
            if not isinstance(message, str):
                raise ValueError("message must be a string")
            metadata = json.loads(
                data.get("metadata", "{}"), parse_constant=_reject_non_finite_json,
            )
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("metadata must be a JSON object or null")
            # Keep this explicit even with strict parsing: it documents the
            # JSONB boundary and rejects non-finite values nested in a value.
            json.dumps(metadata, allow_nan=False)
            logs_to_insert.append(ExecutionLog(
                execution_id=exec_uuid,
                level=level,
                message=message,
                log_metadata=metadata,
                timestamp=timestamp,
                sequence=next_sequence + offset,
            ))
            entry_ids.append(entry_id)
        except Exception as exc:
            # Metadata decoding is part of parsing. Keep this source entry for
            # operator repair and allow later entries to make progress.
            logger.warning("Failed to parse log entry %s: %s", entry_id, exc)

    session.add_all(logs_to_insert)
    return LogFlushReceipt(exec_id, stream_key, tuple(entry_ids))


async def acknowledge_staged_logs(receipt: LogFlushReceipt) -> None:
    """Acknowledge only source entries that are known to have committed."""
    _require_server_process()
    if not receipt.entry_ids:
        return
    try:
        from src.core.cache import get_redis

        async with get_redis() as redis:
            await redis.xdel(receipt.stream_key, *receipt.entry_ids)
    except Exception as exc:
        # A later terminal recovery may replay these rows. Do not turn a
        # durable execution completion back into a failure because Redis is
        # briefly unavailable.
        logger.warning(
            "Failed to acknowledge %d execution logs for %s: %s",
            receipt.count,
            log_safe(receipt.execution_id),
            exc,
        )


async def flush_logs_to_postgres(
    execution_id: str | UUID,
) -> int:
    """Persist stream logs, acknowledging them after an owned commit.

    This owned-session wrapper is for recovery only. Callers participating in
    another transaction must use the explicit staging API and receipt.
    """
    _require_server_process()
    try:
        from src.core.database import get_session_factory

        async with get_session_factory()() as db:
            receipt = await stage_logs_to_postgres(execution_id, db)
            if not receipt.count:
                return 0
            await db.commit()
        await acknowledge_staged_logs(receipt)
        logger.debug("Flushed %d logs to Postgres for %s", receipt.count, log_safe(str(execution_id)))
        return receipt.count
    except Exception as exc:
        logger.error("Failed to flush logs to Postgres: %s", exc)
        return 0


async def recover_terminal_execution_logs(
    execution_ids: Iterable[str | UUID] | None = None,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Boundedly flush streams whose durable executions are terminal.

    Active executions keep their Redis stream for the live reader. A cursor is
    advanced only after the returned scan page has been handled, so a bounded
    pass never drops an unvisited page. Redis SCAN can repeat keys; replay is
    acceptable because acknowledgement is at-least-once.
    """
    _require_server_process()
    from sqlalchemy import select

    from src.core.cache import get_redis
    from src.models.enums import LIVE_EXECUTION_STATUSES
    from src.models.orm import Execution

    requested: list[str] = []
    scanned = 0
    if execution_ids is None:
        async with get_redis() as redis:
            cursor_value = await redis.get(execution_log_recovery_cursor_key())
            cursor = int(cursor_value or 0)
            next_cursor, keys = await redis.scan(
                cursor=cursor,
                match="bifrost:logs:*",
                count=limit,
            )
        for key in keys:
            execution_id = key.removeprefix("bifrost:logs:")
            try:
                UUID(execution_id)
            except ValueError:
                continue
            requested.append(execution_id)
        scanned = len(keys)
    else:
        requested = [str(execution_id) for execution_id in execution_ids][:limit]
        next_cursor = None

    terminal: list[str] = []
    if requested:
        from src.core.database import get_session_factory

        requested_uuids = [UUID(execution_id) for execution_id in requested]
        async with get_session_factory()() as session:
            rows = await session.execute(
                select(Execution.id, Execution.status).where(Execution.id.in_(requested_uuids))
            )
        terminal = [
            str(execution_id)
            for execution_id, status in rows
            if status not in LIVE_EXECUTION_STATUSES
        ]

    flushed = 0
    for execution_id in terminal:
        flushed += await flush_logs_to_postgres(execution_id)

    if next_cursor is not None:
        # Store this only after every key on the returned page was considered.
        async with get_redis() as redis:
            await redis.set(execution_log_recovery_cursor_key(), next_cursor)
    return {"scanned": scanned, "terminal": len(terminal), "flushed": flushed}
