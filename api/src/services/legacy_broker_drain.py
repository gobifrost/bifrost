"""Move work an older release left in RabbitMQ into PostgreSQL (#769).

Releases before the unified runtime queued workflow runs, agent runs and
summaries in RabbitMQ, with a run's context in Redis. An install upgraded
while such messages were waiting still has them there, and nothing else will
ever read them. When ``BIFROST_RABBITMQ_URL`` names a reachable broker, the
trigger leader drains those queues: each message becomes the durable
dispatch the current release would have accepted, then is acknowledged.

- A message that already names a dispatch (``dispatch_id``) came from a
  release that also committed the dispatch; it is only acknowledged.
- A run whose Redis context expired (older releases kept it for an hour)
  cannot be rebuilt. A durable row is failed visibly; a run that never had
  a row is logged.
- Dead-lettered (``-poison``) messages stay where they are: those runs
  already ended.

Without a configured or reachable broker this does nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy import select, update

from src.config import get_settings
from src.core.cache.redis_client import get_redis
from src.core.database import get_dispatch_db_context
from src.core.log_safety import log_safe
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.executions import Execution
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.scheduler.registry import ScheduledTaskOutcome

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5.0
# Messages per queue per pass; the next pass continues.
BATCH = 200
LOST_IN_UPGRADE = "This run was queued by an earlier release and its context expired before it could be recovered."


def _workflow_pending_key(execution_id: str) -> str:
    return f"bifrost:exec:{execution_id}:pending"


def _agent_context_key(run_id: str) -> str:
    return f"bifrost:agent_run:{run_id}:context"


async def _redis_json(key: str) -> dict[str, Any] | None:
    async with get_redis() as redis:
        raw = await redis.get(key)
    return json.loads(raw) if raw else None


async def _forget(key: str) -> None:
    async with get_redis() as redis:
        await redis.delete(key)


async def recover_workflow_run(body: dict[str, Any]) -> None:
    """A ``workflow-executions`` message: accept the run as this release would."""
    from src.core.redis_client import PendingExecution
    from src.services.execution.workflow_dispatch import (
        hand_off_workflow_run,
        stage_workflow_run,
    )

    if body.get("dispatch_id"):
        return
    execution_id = body["execution_id"]
    record_exists = bool(body.get("execution_record_exists"))
    pending = await _redis_json(_workflow_pending_key(execution_id))
    if pending is None or pending.get("cancelled"):
        if record_exists:
            async with get_dispatch_db_context() as db:
                await db.execute(
                    update(Execution)
                    .where(
                        Execution.id == UUID(execution_id),
                        Execution.status == ExecutionStatus.PENDING,
                    )
                    .values(
                        status=ExecutionStatus.CANCELLED
                        if pending is not None
                        else ExecutionStatus.FAILED,
                        error_message=None if pending is not None else LOST_IN_UPGRADE,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
        else:
            logger.warning(
                "A queued run from an earlier release could not be recovered",
                extra={"execution_id": log_safe(execution_id), "cancelled": pending is not None},
            )
        return
    message = {
        key: body[key]
        for key in ("execution_id", "workflow_id", "code", "script_name", "sync", "dispatch_metadata")
        if key in body
    }
    async with get_dispatch_db_context() as db:
        if record_exists:
            status = await db.scalar(
                select(Execution.status)
                .where(Execution.id == UUID(execution_id))
                .with_for_update()
            )
            if status != ExecutionStatus.PENDING:
                return
        accepted = await stage_workflow_run(
            db,
            PendingExecution(**pending),  # type: ignore[typeddict-item]
            message,
            record_exists=record_exists,
        )
        await db.commit()
    await hand_off_workflow_run(accepted)
    await _forget(_workflow_pending_key(execution_id))


async def recover_agent_run(body: dict[str, Any]) -> None:
    """An ``agent-runs`` message: the queued row gets its dispatch."""
    from src.services.execution.agent_run_dispatch import hand_off_agent_run, stage_agent_run

    if body.get("dispatch_id"):
        return
    run_id = body["run_id"]
    context = await _redis_json(_agent_context_key(run_id))
    async with get_dispatch_db_context() as db:
        run = await db.get(AgentRun, UUID(run_id), with_for_update={"of": AgentRun})
        if run is None or run.status != "queued":
            return
        if context is None or context.get("cancelled"):
            run.status = "cancelled" if context is not None else "failed"
            run.error = None if context is not None else LOST_IN_UPGRADE
            run.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return
        context.pop("cancelled", None)
        stage_agent_run(db, run_id=UUID(run_id), context=context, message=body)
        await db.commit()
    await hand_off_agent_run()
    await _forget(_agent_context_key(run_id))


async def _recover_summary(body: dict[str, Any], *, backfill: bool) -> None:
    from src.services.execution.summary_dispatch import hand_off_summary, stage_summary

    if body.get("dispatch_id"):
        return
    run_id = UUID(body["run_id"])
    async with get_dispatch_db_context() as db:
        run = await db.get(AgentRun, run_id, with_for_update={"of": AgentRun})
        if run is None or run.status != "completed" or run.summary_status not in (None, "pending"):
            return
        run.summary_status = "pending"
        job_id = body.get("backfill_job_id")
        if job_id and await db.get(SummaryBackfillJob, UUID(job_id)) is not None:
            run.summary_backfill_job_id = UUID(job_id)
        dispatch = await stage_summary(db, run_id, backfill=backfill)
        await db.commit()
    await hand_off_summary(dispatch)


async def recover_summary(body: dict[str, Any]) -> None:
    """An ``agent-summarization`` message."""
    await _recover_summary(body, backfill=False)


async def recover_backfill_summary(body: dict[str, Any]) -> None:
    """An ``agent-summarization-backfill`` message."""
    await _recover_summary(body, backfill=True)


HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
    "workflow-executions": recover_workflow_run,
    "agent-runs": recover_agent_run,
    "agent-summarization": recover_summary,
    "agent-summarization-backfill": recover_backfill_summary,
}


async def _drain_queue(connection: aio_pika.abc.AbstractConnection, name: str) -> int:
    """Recover up to BATCH messages; a failure leaves its message queued."""
    channel = await connection.channel()
    try:
        queue = await channel.declare_queue(name, passive=True)
    except aio_pika.exceptions.ChannelNotFoundEntity:
        return 0  # This install never had the queue.
    recovered = 0
    try:
        for _ in range(BATCH):
            message: AbstractIncomingMessage | None = await queue.get(no_ack=False, fail=False)
            if message is None:
                break
            try:
                await HANDLERS[name](json.loads(message.body))
            except Exception:
                logger.exception("Could not recover a queued message", extra={"queue": name})
                await message.nack(requeue=True)
                break
            await message.ack()
            recovered += 1
    finally:
        await channel.close()
    return recovered


async def drain_legacy_broker() -> ScheduledTaskOutcome:
    """Scheduled by the trigger leader; see the module docstring."""
    url = get_settings().rabbitmq_url
    if not url:
        return ScheduledTaskOutcome(summary="No legacy broker configured")
    try:
        connection = await asyncio.wait_for(aio_pika.connect(url), CONNECT_TIMEOUT_SECONDS)
    except Exception:
        return ScheduledTaskOutcome(summary="Legacy broker unreachable")
    async with connection:
        counts = {name: await _drain_queue(connection, name) for name in HANDLERS}
    recovered = sum(counts.values())
    if recovered:
        logger.info("Recovered work queued by an earlier release", extra={"counts": counts})
    return ScheduledTaskOutcome(summary=f"Recovered {recovered} queued item(s)")
