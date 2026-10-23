"""Durable dispatch of agent runs.

An agent run is accepted as a ``queued`` ``AgentRun`` plus a ``WorkDispatch``
holding its execution context, committed together. The worker claims the run
under its row lock and takes the context by deleting the dispatch in the same
transaction (run first, then dispatch: the reconciler's lock order too).

One-release bridge (remove in #769 stage 5): the context is also written to
Redis, best-effort, so a worker that predates dispatches can still run it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cache.redis_client import get_redis
from src.core.log_safety import log_safe
from src.models.orm.agent_runs import AgentRun
from src.models.orm.work_dispatches import WorkDispatch
from src.services.work_dispatch import hand_off, stage_dispatch

logger = logging.getLogger(__name__)

FAMILY = "agent_run"
HANDLER = "agent.run"
HANDLER_VERSION = 1
QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"
CONTEXT_TTL_SECONDS = 3600


def stage_agent_run(
    db: AsyncSession,
    *,
    run_id: UUID,
    context: dict[str, Any],
    message: dict[str, Any],
) -> WorkDispatch:
    """Add the run's dispatch to the caller's transaction (with its row)."""
    return stage_dispatch(
        db,
        family=FAMILY,
        subject_id=run_id,
        handler=HANDLER,
        handler_version=HANDLER_VERSION,
        queue=QUEUE_NAME,
        message=message,
        payload=context,
    )


async def cache_agent_context(run_id: str, context: dict[str, Any]) -> None:
    """Bridge copy for workers that predate dispatches; best-effort."""
    try:
        async with get_redis() as redis:
            await redis.set(
                f"{REDIS_PREFIX}:{run_id}:context",
                json.dumps(context),
                ex=CONTEXT_TTL_SECONDS,
            )
    except Exception:
        logger.warning(
            "Could not cache agent run context for older workers",
            extra={"run_id": log_safe(run_id)},
            exc_info=True,
        )


async def hand_off_agent_run(dispatch: WorkDispatch) -> bool:
    return await hand_off(dispatch.id, dispatch.placement, QUEUE_NAME, dispatch.message)


async def lock_waiting_agent_run(db: AsyncSession, run_id: UUID) -> bool | None:
    """Lock the run before its dispatch (the claim's order); None if busy."""
    status = await db.scalar(
        select(AgentRun.status).where(AgentRun.id == run_id).with_for_update(skip_locked=True)
    )
    if status is not None:
        return status == "queued"
    exists = await db.scalar(select(AgentRun.id).where(AgentRun.id == run_id))
    return None if exists is not None else False


async def fail_unclaimed_agent_run(db: AsyncSession, run_id: UUID, reason: str) -> None:
    await db.execute(
        update(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.status == "queued")
        .values(status="failed", error=reason, completed_at=datetime.now(timezone.utc))
    )


async def report_unclaimed_agent_run(
    run_id: UUID, payload: dict[str, Any], reason: str
) -> None:
    if not payload.get("sync"):
        return
    try:
        async with get_redis() as redis:
            key = f"{REDIS_PREFIX}:{run_id}:result"
            await redis.lpush(  # pyright: ignore[reportGeneralTypeIssues]
                key, json.dumps({"output": None, "status": "failed", "error": reason})
            )
            await redis.expire(key, 300)
    except Exception:
        logger.warning(
            "Could not wake the caller of an unclaimed agent run",
            extra={"run_id": str(run_id)},
            exc_info=True,
        )


__all__ = [
    "FAMILY",
    "HANDLER",
    "HANDLER_VERSION",
    "QUEUE_NAME",
    "cache_agent_context",
    "fail_unclaimed_agent_run",
    "hand_off_agent_run",
    "lock_waiting_agent_run",
    "report_unclaimed_agent_run",
    "stage_agent_run",
]
