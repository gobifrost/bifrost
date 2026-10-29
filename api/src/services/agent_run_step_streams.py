"""Lifecycle operations for transient agent-run step Redis streams."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cache.keys import agent_run_steps_stream_key
from src.core.cache.redis_client import get_redis
from src.models.orm import AgentRun

logger = logging.getLogger(__name__)

AGENT_RUN_STEP_STREAM_TTL_SECONDS = 24 * 60 * 60
AGENT_RUN_STEP_STREAM_MAXLEN = 1000
AGENT_RUN_STEP_STREAM_SCAN_LIMIT = 100
AGENT_RUN_STEP_STREAM_DELETE_BATCH_SIZE = 500
_AGENT_RUN_STEP_STREAM_PREFIX = agent_run_steps_stream_key("")


async def append_agent_run_step(
    redis: Redis,
    *,
    run_id: UUID | str,
    fields: Mapping[str, str],
) -> str:
    """Append a step and refresh its stream's expiry as one Redis transaction."""
    stream_key = agent_run_steps_stream_key(str(run_id))
    async with redis.pipeline(transaction=True) as pipeline:
        pipeline.xadd(stream_key, fields, maxlen=AGENT_RUN_STEP_STREAM_MAXLEN)
        pipeline.expire(stream_key, AGENT_RUN_STEP_STREAM_TTL_SECONDS)
        results = await pipeline.execute()
    return str(results[0])


def _run_id_from_stream_key(stream_key: str) -> UUID | None:
    if not stream_key.startswith(_AGENT_RUN_STEP_STREAM_PREFIX):
        return None
    try:
        return UUID(stream_key.removeprefix(_AGENT_RUN_STEP_STREAM_PREFIX))
    except ValueError:
        return None


def _stream_entry_timestamp(entry_id: str) -> datetime | None:
    try:
        milliseconds = int(entry_id.split("-", maxsplit=1)[0])
        return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


async def inspect_orphaned_agent_run_step_streams(
    db: AsyncSession,
    redis: Redis,
    *,
    cursor: int = 0,
    limit: int = AGENT_RUN_STEP_STREAM_SCAN_LIMIT,
    now: datetime | None = None,
) -> dict[str, int]:
    """Inspect one bounded page of possible orphan streams without mutating Redis.

    A stream is an orphan only when its UUID has no matching ``AgentRun`` row
    and its newest entry is older than the normal stream TTL. The result is
    deliberately aggregate-only so an operational preview does not expose run
    identities or Redis keys. Redis treats ``COUNT`` as a hint, so a response
    larger than ``limit`` is marked incomplete and only the first ``limit``
    keys are inspected. The returned cursor advances beyond that response;
    restart from cursor zero for a fresh bounded sample when incomplete.
    """
    if not 1 <= limit <= AGENT_RUN_STEP_STREAM_SCAN_LIMIT:
        raise ValueError(f"limit must be between 1 and {AGENT_RUN_STEP_STREAM_SCAN_LIMIT}")
    if cursor < 0:
        raise ValueError("cursor must not be negative")
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    next_cursor, returned_keys = await redis.scan(
        cursor,
        match=f"{_AGENT_RUN_STEP_STREAM_PREFIX}*",
        count=limit,
    )
    keys = list(returned_keys[:limit])
    stream_ids_by_key = {
        key: run_id
        for key in keys
        if (run_id := _run_id_from_stream_key(key)) is not None
    }
    existing_run_ids = set(
        (
            await db.scalars(
                select(AgentRun.id).where(AgentRun.id.in_(set(stream_ids_by_key.values())))
            )
        ).all()
    ) if stream_ids_by_key else set()

    report = {
        "next_cursor": int(next_cursor),
        "scanned": len(keys),
        "uninspected_keys": max(0, len(returned_keys) - len(keys)),
        "invalid_keys": len(keys) - len(stream_ids_by_key),
        "protected_existing_runs": 0,
        "recent_missing_runs": 0,
        "orphaned_streams": 0,
        "empty_or_disappeared_streams": 0,
        "unparseable_newest_entry": 0,
        "non_stream_keys": 0,
    }
    expiry_cutoff = now - timedelta(seconds=AGENT_RUN_STEP_STREAM_TTL_SECONDS)
    for stream_key, run_id in stream_ids_by_key.items():
        if run_id in existing_run_ids:
            report["protected_existing_runs"] += 1
            continue
        try:
            entries = await redis.xrevrange(stream_key, max="+", min="-", count=1)
        except ResponseError as exc:
            if not str(exc).startswith("WRONGTYPE"):
                raise
            report["non_stream_keys"] += 1
            continue
        if not entries:
            report["empty_or_disappeared_streams"] += 1
            continue
        newest_at = _stream_entry_timestamp(str(entries[0][0]))
        if newest_at is None:
            report["unparseable_newest_entry"] += 1
        elif newest_at < expiry_cutoff:
            report["orphaned_streams"] += 1
        else:
            report["recent_missing_runs"] += 1
    return report


async def delete_agent_run_step_streams(run_ids: Sequence[UUID]) -> None:
    """Best-effort cleanup for streams whose agent-run rows were deleted."""
    if not run_ids:
        return
    try:
        async with get_redis() as redis:
            for start in range(0, len(run_ids), AGENT_RUN_STEP_STREAM_DELETE_BATCH_SIZE):
                keys = [
                    agent_run_steps_stream_key(str(run_id))
                    for run_id in run_ids[start : start + AGENT_RUN_STEP_STREAM_DELETE_BATCH_SIZE]
                ]
                await redis.delete(*keys)
    except Exception as exc:
        # Stream TTL remains the safety net; do not turn a completed retention
        # transaction into a failed job because Redis cleanup is unavailable.
        logger.warning(
            "Failed to remove %d agent-run step streams after run retention (%s)",
            len(run_ids),
            type(exc).__name__,
        )
