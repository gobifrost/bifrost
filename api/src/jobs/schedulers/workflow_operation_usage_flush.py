"""Flush the Redis workflow-operation-usage buffer into the durable table.

Every request authenticated by an engine token carrying an
``engine_workflow_id`` claim (see ``mint_engine_token`` /
``src.core.app_wiring._record_workflow_operation_usage``) increments a
per-UTC-day Redis hash ``bifrost:wf_usage:<YYYY-MM-DD>``, field
``<workflow_id>|<operation_key>``. This job runs every 15 minutes (see
``src/scheduler/main.py``), atomically drains today's and yesterday's
hashes, and upserts durable per-(workflow, operation, day) counts into
``workflow_operation_usage``.

Attribution only, leader-only housekeeping — no PlatformJob needed (see
docs/architecture/platform-jobs.md: "Small, idempotent housekeeping may
remain leader-only scheduler work.").
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import get_settings
from src.core.database import get_db_context
from src.models.orm.workflow_operation_usage import WorkflowOperationUsage
from src.models.orm.workflows import Workflow

logger = logging.getLogger(__name__)

WF_USAGE_KEY_PREFIX = "bifrost:wf_usage:"


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


async def _drain_day_key(r: aioredis.Redis, day: date) -> dict[str, str]:
    """Atomically drain one UTC day's usage hash.

    ``RENAME`` to a private, timestamped temp key is atomic, so a concurrent
    ``HINCRBY`` from an in-flight request either lands before the rename
    (drained this run) or after it recreates the key (drained next run) —
    never lost and never double-counted.
    """
    key = f"{WF_USAGE_KEY_PREFIX}{day.isoformat()}"
    temp_key = f"{key}:draining:{datetime.now(timezone.utc).timestamp()}"
    try:
        await r.rename(key, temp_key)
    except aioredis.ResponseError as exc:
        # "no such key" — nothing accumulated for this day since the last
        # flush. Any other rename failure is a real problem; let it raise.
        if "no such key" in str(exc).lower():
            return {}
        raise
    try:
        data: dict[str, str] = await r.hgetall(temp_key)  # type: ignore[assignment]
        return data
    finally:
        await r.delete(temp_key)


async def flush_workflow_operation_usage() -> int:
    """Drain today's and yesterday's usage hashes into the durable table.

    Returns the number of (workflow_id, operation_key, day) rows upserted.
    Idempotent: a flush with nothing newly buffered (e.g. a second call
    shortly after the first) upserts zero rows, since draining a hash
    empties it.
    """
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    r = await _get_redis()
    try:
        drained: dict[date, dict[str, str]] = {}
        for day in (yesterday, today):
            drained[day] = await _drain_day_key(r, day)
    finally:
        await _close_redis(r)

    rows: list[dict] = []
    for day, fields in drained.items():
        for field, raw_count in fields.items():
            workflow_id_str, sep, operation_key = field.partition("|")
            if not sep:
                logger.debug("Dropping malformed workflow usage field %r", field)
                continue
            try:
                workflow_id = UUID(workflow_id_str)
                count = int(raw_count)
            except ValueError:
                logger.debug("Dropping malformed workflow usage field %r", field)
                continue
            rows.append(
                {
                    "workflow_id": workflow_id,
                    "operation_key": operation_key,
                    "day": day,
                    "count": count,
                }
            )

    if not rows:
        return 0

    async with get_db_context() as db:
        known_ids = set(
            (
                await db.execute(
                    select(Workflow.id).where(
                        Workflow.id.in_({row["workflow_id"] for row in rows})
                    )
                )
            )
            .scalars()
            .all()
        )
        # A workflow deleted since the request was recorded has no subject
        # left to attribute usage to; drop rather than fail the FK insert.
        upsertable = [row for row in rows if row["workflow_id"] in known_ids]
        if not upsertable:
            return 0

        stmt = pg_insert(WorkflowOperationUsage).values(upsertable)
        stmt = stmt.on_conflict_do_update(
            index_elements=["workflow_id", "operation_key", "day"],
            set_={"count": WorkflowOperationUsage.count + stmt.excluded.count},
        )
        await db.execute(stmt)

    return len(upsertable)
