"""
Trailing service-log persistence: Redis stream drain into Postgres.

The owning worker's claim loop calls ``flush_attempt_logs`` on every beat
for each owned attempt (bounded batch), plus one final drain when the
attempt completes. Delivery is at-least-once: the flush stages rows in
the caller's DB transaction and returns the last stream ID, but the
Redis cursor advances only AFTER the transaction commits (via
``store_log_cursor``). A crash between commit and cursor-store replays a
bounded duplicate tail; a commit failure replays cleanly with no
duplicates. The per-attempt Redis cursor survives owner takeover, so
failover resumes without re-reading ancient history. Retention trims each
service to its newest ``SERVICE_LOG_RETAIN_PER_SERVICE`` rows inside the
same flush — no scheduler involved.

Cap size is a guess; Slice 6 tunes it with live measurements.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.services import ServiceLog

logger = logging.getLogger(__name__)

#: Max stream entries drained into Postgres per flush call.
SERVICE_LOG_FLUSH_BATCH = 500

#: Trailing rows retained per service (newest wins). Guess — Slice 6 tuning.
SERVICE_LOG_RETAIN_PER_SERVICE = 2000


def _parse_entry(data: dict[Any, Any]) -> tuple[str, str, datetime] | None:
    """One stream entry -> (level, message, timestamp); None when unusable."""
    try:
        level = str(data.get("level", "INFO")).upper() or "INFO"
        message = str(data.get("message", ""))
        ts = datetime.fromisoformat(str(data.get("timestamp", "")))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return level, message, ts
    except (ValueError, TypeError, AttributeError):
        return None


async def flush_attempt_logs(
    db: AsyncSession,
    service_id: UUID,
    attempt_id: UUID,
    *,
    batch: int = SERVICE_LOG_FLUSH_BATCH,
) -> tuple[int, str | None]:
    """Drain one attempt's stream tail into the caller's transaction.

    Reads at most ``batch`` entries after the Redis cursor and inserts
    them (caller commits). Returns ``(rows_inserted, last_stream_id)``;
    the caller persists the cursor with ``store_log_cursor`` only after
    a successful commit — never before. Returns ``(0, None)`` on Redis
    failure. Never raises: persistence is best-effort and must not break
    supervision.
    """
    from src.core.cache import get_redis
    from src.core.cache.keys import (
        service_logs_cursor_key,
        service_logs_stream_key,
    )

    stream_key = service_logs_stream_key(str(attempt_id))
    try:
        async with get_redis() as r:
            cursor = await cast(
                Awaitable[str | bytes | None],
                r.get(service_logs_cursor_key(str(attempt_id))),
            )
            start = (
                cursor.decode()
                if isinstance(cursor, (bytes, bytearray))
                else (cursor or "0")
            )
            # Exclusive range after the last flushed ID ("-" for first flush).
            min_arg = f"({start}" if start != "0" else "-"
            entries = await cast(
                Awaitable[list[tuple[str, dict[Any, Any]]]],
                r.xrange(stream_key, min=min_arg, max="+", count=batch),  # type: ignore[misc]
            )
            rows = []
            last_id: str | None = None
            for entry_id, data in entries:
                parsed = _parse_entry(data)
                last_id = entry_id
                if parsed is None:
                    continue
                level, message, ts = parsed
                rows.append(
                    ServiceLog(
                        service_id=service_id,
                        attempt_id=attempt_id,
                        level=level,
                        message=message,
                        timestamp=ts,
                    )
                )
            if rows:
                db.add_all(rows)
                await db.flush()
    except Exception as e:
        logger.debug(
            "service log flush failed for %s: %s", attempt_id, e
        )
        return 0, None
    if rows:
        await trim_service_logs(db, service_id)
    return len(rows), last_id


async def store_log_cursor(attempt_id: UUID, last_id: str) -> bool:
    """Advance one attempt's flush cursor (call only after commit).

    TTL refreshes while the attempt is live; dead attempts' cursors
    expire on their own (completion also clears). Returns False (never
    raises) when Redis is unreachable so the caller can retry next tick.
    """
    from src.core.cache import get_redis
    from src.core.cache.keys import service_logs_cursor_key

    try:
        async with get_redis() as r:
            await r.setex(
                service_logs_cursor_key(str(attempt_id)), 7 * 86400, last_id
            )
    except Exception as e:
        logger.debug(
            "service log cursor store failed for %s: %s", attempt_id, e
        )
        return False
    return True


async def trim_service_logs(
    db: AsyncSession,
    service_id: UUID,
    retain: int = SERVICE_LOG_RETAIN_PER_SERVICE,
) -> int:
    """Delete rows beyond the newest ``retain`` for one service."""
    keeper_ids = (
        select(ServiceLog.id)
        .where(ServiceLog.service_id == service_id)
        .order_by(ServiceLog.id.desc())
        .limit(retain)
    )
    result = await db.execute(
        delete(ServiceLog).where(
            ServiceLog.service_id == service_id,
            ServiceLog.id.notin_(keeper_ids),
        )
    )
    await db.flush()
    return result.rowcount or 0


async def clear_log_cursor(attempt_id: UUID) -> None:
    """Drop one attempt's flush cursor (terminal completion)."""
    from src.core.cache import get_redis
    from src.core.cache.keys import service_logs_cursor_key

    try:
        async with get_redis() as r:
            await r.delete(service_logs_cursor_key(str(attempt_id)))
    except Exception as e:
        logger.debug(
            "service log cursor cleanup failed for %s: %s", attempt_id, e
        )


async def count_service_logs(db: AsyncSession, service_id: UUID) -> int:
    """Rows currently retained for one service (trim verification)."""
    return (
        await db.scalar(
            select(func.count(ServiceLog.id)).where(
                ServiceLog.service_id == service_id
            )
        )
        or 0
    )
