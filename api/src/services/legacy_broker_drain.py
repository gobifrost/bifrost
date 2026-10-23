"""Move work an older release left in RabbitMQ into PostgreSQL (#769).

Releases before the unified runtime queued workflow runs, agent runs and
summaries in RabbitMQ, with a run's context in Redis. An install upgraded
while such messages were waiting still has them there, and nothing else will
ever read them. When ``BIFROST_RABBITMQ_URL`` is set, each API replica tries
to reach the broker a few times once it starts, drains those queues until they
are empty, disconnects and stops; there is no recurring poll. Each message
becomes the durable dispatch the current release would have accepted, then is
acknowledged. Several replicas draining at once is safe: the broker gives each
message to one consumer and a subject has at most one dispatch.

- A message that already names a dispatch (``dispatch_id``) came from a
  release that also committed the dispatch; it is only acknowledged.
- Those releases recorded only the user a run executes as; a recovered run
  runs for that user (``shared.run_lineage.recorded_user_lineage``).
- A run cancelled while queued is recorded as cancelled, as those releases
  did.
- A run whose Redis context expired (older releases kept it for an hour)
  cannot be rebuilt. A durable row is failed visibly; a run that never had
  a row is logged.
- Dead-lettered (``-poison``) messages stay where they are: those runs
  already ended.
- A message that cannot be recovered stays queued; that queue's drain stops
  and the next start tries again.

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

from shared.run_lineage import lineage_columns, recorded_user_lineage
from shared.schema_gate import wait_for_schema
from src.config import get_settings
from src.core.cache.redis_client import get_redis
from src.core.database import get_dispatch_db_context, get_engine
from src.core.log_safety import log_safe
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.executions import Execution
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.models.orm.workflows import Workflow

logger = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3
CONNECT_TIMEOUT_SECONDS = 5.0
CONNECT_RETRY_SECONDS = 5.0
CANCELLED_BEFORE_START = "Execution was cancelled before it could start"
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


async def _record_cancelled_run(pending: dict[str, Any]) -> None:
    """A run cancelled while queued, which never had a row: record it as that release did."""
    workflow_id = UUID(pending["workflow_id"]) if pending.get("workflow_id") else None
    async with get_dispatch_db_context() as db:
        name = None if workflow_id is None else await db.scalar(
            select(Workflow.name).where(Workflow.id == workflow_id)
        )
        db.add(
            Execution(
                id=UUID(pending["execution_id"]),
                workflow_id=workflow_id,
                workflow_name=name or pending.get("script_name") or "script",
                status=ExecutionStatus.CANCELLED,
                parameters=pending.get("parameters") or {},
                organization_id=UUID(pending["org_id"]) if pending.get("org_id") else None,
                executed_by=UUID(pending["user_id"]),
                executed_by_name=pending.get("user_name") or "",
                error_message=CANCELLED_BEFORE_START,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()


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
                        error_message=CANCELLED_BEFORE_START if pending is not None else LOST_IN_UPGRADE,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
        elif pending is not None:
            await _record_cancelled_run(pending)
        else:
            logger.warning(
                "A queued run from an earlier release could not be recovered",
                extra={"execution_id": log_safe(execution_id)},
            )
        await _forget(_workflow_pending_key(execution_id))
        return
    message = {
        key: body[key]
        for key in ("execution_id", "workflow_id", "code", "script_name", "sync", "dispatch_metadata")
        if key in body
    }
    async with get_dispatch_db_context() as db:
        execution: Execution | None = None
        if record_exists:
            execution = await db.get(Execution, UUID(execution_id), with_for_update=True)
            if execution is None or execution.status != ExecutionStatus.PENDING:
                return
        if pending.get("lineage") is None:
            lineage = await recorded_user_lineage(
                db,
                pending["user_id"],
                workflow_id=pending["workflow_id"],
                organization_id=pending["org_id"],
            )
            pending["lineage"] = lineage.bound(execution_id)
            if execution is not None and execution.run_user_id is None:
                for column, value in lineage_columns(pending["lineage"]).items():
                    setattr(execution, column, value)
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
        if run.run_user_id is None:
            lineage = await recorded_user_lineage(
                db, run.caller_user_id, workflow_id=None, organization_id=run.org_id
            )
            run.run_user_id = lineage.run_user_id
            if run.acting_user_id is None:
                run.acting_user_id = lineage.run_user_id
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
    """Recover messages until the queue is empty; a failure leaves its message queued."""
    channel = await connection.channel()
    try:
        queue = await channel.declare_queue(name, passive=True)
    except aio_pika.exceptions.ChannelNotFoundEntity:
        return 0  # This install never had the queue.
    recovered = 0
    try:
        while True:
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


async def _connect(url: str) -> aio_pika.abc.AbstractConnection | None:
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return await asyncio.wait_for(aio_pika.connect(url), CONNECT_TIMEOUT_SECONDS)
        except Exception:
            if attempt < CONNECT_ATTEMPTS:
                await asyncio.sleep(CONNECT_RETRY_SECONDS)
    return None


async def drain_legacy_broker() -> None:
    """Run once per API start; see the module docstring."""
    url = get_settings().rabbitmq_url
    if not url:
        return
    try:
        await wait_for_schema(get_engine())
        connection = await _connect(url)
        if connection is None:
            logger.info("The legacy broker is unreachable; nothing to recover")
            return
        async with connection:
            counts = {name: await _drain_queue(connection, name) for name in HANDLERS}
        logger.info("Drained work queued by an earlier release", extra={"counts": counts})
    except Exception:
        logger.exception("Could not drain the legacy broker; the next start tries again")
