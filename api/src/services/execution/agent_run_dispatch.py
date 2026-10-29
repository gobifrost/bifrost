"""Durable dispatch of agent runs.

An agent run is accepted as a ``queued`` ``AgentRun`` plus a ``WorkDispatch``
holding its execution context, committed together. The worker claims the run
under its row lock and takes the context by deleting the dispatch in the same
transaction (run first, then dispatch: the reconciler's lock order too).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cache.redis_client import get_redis
from src.core.database import get_db_context
from src.models.orm.agent_runs import AgentRun
from src.models.orm.execution_workers import ExecutionWorkerClaim
from src.models.orm.work_dispatches import WorkDispatch
from src.services.memory_requests import AGENT_RUN_REQUEST_BYTES
from src.services.work_dispatch import hand_off, stage_dispatch

logger = logging.getLogger(__name__)

FAMILY = "agent_run"
HANDLER = "agent.run"
HANDLER_VERSION = 1
REDIS_PREFIX = "bifrost:agent_run"


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
        message=message,
        payload=context,
        memory_request_bytes=AGENT_RUN_REQUEST_BYTES,
    )


async def hand_off_agent_run() -> None:
    await hand_off()


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


async def release_agent_run(*, worker_id: UUID, run_id: UUID, dispatch_id: UUID) -> None:
    """Hand a claimed run back before its child started: another worker takes it.

    For a launcher that refused the fork (memory rose after the claim). The
    dispatch keeps its place in line; the claim record goes, as if this
    worker had never taken it.
    """
    async with get_db_context() as db:
        await db.execute(
            update(WorkDispatch)
            .where(WorkDispatch.id == dispatch_id, WorkDispatch.assigned_worker_id == worker_id)
            .values(assigned_worker_id=None)
        )
        await db.execute(
            delete(ExecutionWorkerClaim).where(
                ExecutionWorkerClaim.worker_id == worker_id,
                ExecutionWorkerClaim.family == FAMILY,
                ExecutionWorkerClaim.subject_id == run_id,
            )
        )
        await db.commit()
    await hand_off()


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
    "fail_unclaimed_agent_run",
    "hand_off_agent_run",
    "lock_waiting_agent_run",
    "release_agent_run",
    "report_unclaimed_agent_run",
    "stage_agent_run",
]
