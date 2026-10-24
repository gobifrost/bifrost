"""Move work an older release left in RabbitMQ into PostgreSQL (#769).

Releases before the unified runtime queued workflow runs, agent runs and
summaries in RabbitMQ, with a run's context in Redis. An install upgraded
while such messages were waiting still has them there, and nothing else will
ever read them. When ``BIFROST_RABBITMQ_URL`` is set, each API replica tries
to reach the broker a few times once it starts, drains those queues until they
have stayed empty for ``QUIET_SECONDS``, disconnects and stops; there is no
recurring poll. Each message
becomes the durable dispatch the current release would have accepted, then is
acknowledged. Replicas drain one at a time, under an advisory lock held for
the whole drain: a replica that saw empty queues while another still held an
unacknowledged message must not record the drain, or that message, requeued
if its holder dies, would never be recovered. A replica that waited finds the
drain recorded and stops; one that waited behind a drain that ended early
drains itself.

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

A drain that reached the broker and left nothing queued is recorded
(``SystemConfig`` ``runtime`` / ``legacy_broker_drained``). From then on no
start connects, and ``BIFROST_RABBITMQ_URL`` is reported as unread. A rollback
to a release that queues to RabbitMQ again needs that row deleted.

Without a configured or reachable broker this does nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.run_lineage import lineage_columns, recorded_user_lineage
from shared.schema_gate import wait_for_schema
from src.config import get_settings
from src.core.cache.redis_client import get_redis
from src.core.database import get_db_context, get_dispatch_db_context, get_engine
from src.core.log_safety import log_safe
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.config import SystemConfig
from src.models.orm.executions import Execution
from src.models.orm.summary_backfill_job import SummaryBackfillJob
from src.models.orm.workflows import Workflow

logger = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 3
CONNECT_TIMEOUT_SECONDS = 5.0
CONNECT_RETRY_SECONDS = 5.0
# Empty means empty for this long, so work an older replica queues during a
# rolling upgrade, after this one first drained, is still recovered.
QUIET_SECONDS = 120.0
PASS_INTERVAL_SECONDS = 10.0
CANCELLED_BEFORE_START = "Execution was cancelled before it could start"
LOST_IN_UPGRADE = "This run was queued by an earlier release and its context expired before it could be recovered."
DRAINED_CATEGORY = "runtime"
DRAINED_KEY = "legacy_broker_drained"
DRAIN_LOCK = "bifrost:legacy-broker-drain"


async def legacy_broker_drained(db: AsyncSession) -> bool:
    """Whether a drain on this release finished (see the module docstring)."""
    return (
        await db.scalar(
            select(SystemConfig.id)
            .where(
                SystemConfig.category == DRAINED_CATEGORY,
                SystemConfig.key == DRAINED_KEY,
                SystemConfig.organization_id.is_(None),
            )
            .limit(1)
        )
    ) is not None


async def _record_drained() -> None:
    """Only the drain holding ``DRAIN_LOCK``, which found no record, writes it."""
    async with get_db_context() as db:
        db.add(
            SystemConfig(
                id=uuid4(),
                category=DRAINED_CATEGORY,
                key=DRAINED_KEY,
                value_json={"drained_at": datetime.now(timezone.utc).isoformat()},
                organization_id=None,
                created_by="system",
                updated_by="system",
            )
        )
        await db.commit()


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


async def _drain_queue(
    connection: aio_pika.abc.AbstractConnection, name: str
) -> tuple[int, bool]:
    """Recover messages until the queue is empty; a failure leaves its message
    queued. Returns how many were recovered and whether the queue emptied."""
    channel = await connection.channel()
    try:
        queue = await channel.declare_queue(name, passive=True)
    except aio_pika.exceptions.ChannelNotFoundEntity:
        return 0, True  # This install never had the queue.
    recovered = 0
    try:
        while True:
            message: AbstractIncomingMessage | None = await queue.get(no_ack=False, fail=False)
            if message is None:
                return recovered, True
            try:
                await HANDLERS[name](json.loads(message.body))
            except Exception:
                logger.exception("Could not recover a queued message", extra={"queue": name})
                await message.nack(requeue=True)
                return recovered, False
            await message.ack()
            recovered += 1
    finally:
        await channel.close()


async def _connect(url: str) -> aio_pika.abc.AbstractConnection | None:
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return await asyncio.wait_for(aio_pika.connect(url), CONNECT_TIMEOUT_SECONDS)
        except Exception:
            if attempt < CONNECT_ATTEMPTS:
                await asyncio.sleep(CONNECT_RETRY_SECONDS)
    return None


async def _drain(url: str) -> None:
    connection = await _connect(url)
    if connection is None:
        logger.info(
            "The legacy broker is unreachable; nothing to recover. Remove "
            "BIFROST_RABBITMQ_URL once its queues are known to be empty; it is "
            "reported as unused only after a drain reaches the broker"
        )
        return
    async with connection:
        counts = dict.fromkeys(HANDLERS, 0)
        left_queued: set[str] = set()
        quiet_since = time.monotonic()
        while True:
            for name in HANDLERS:
                recovered, emptied = await _drain_queue(connection, name)
                counts[name] += recovered
                if emptied:
                    left_queued.discard(name)
                else:
                    left_queued.add(name)
                if recovered:
                    quiet_since = time.monotonic()
            if time.monotonic() - quiet_since >= QUIET_SECONDS:
                break
            await asyncio.sleep(PASS_INTERVAL_SECONDS)
    if left_queued:
        logger.warning(
            "Drained work queued by an earlier release; some stays queued "
            "and the next start tries again",
            extra={"counts": counts, "queues": sorted(left_queued)},
        )
        return
    await _record_drained()
    logger.info(
        "Drained work queued by an earlier release; BIFROST_RABBITMQ_URL "
        "can now be removed",
        extra={"counts": counts},
    )


async def drain_legacy_broker() -> None:
    """Run once per API start; see the module docstring."""
    url = get_settings().rabbitmq_url
    if not url:
        return
    try:
        engine = get_engine()
        await wait_for_schema(engine)
        # One drain at a time across replicas, held until this one ends. The
        # lock is transaction-scoped because PgBouncer pools by transaction;
        # the connection's open transaction holds it, and a replica that dies
        # mid-drain releases it with its connection. A PgBouncer
        # idle_transaction_timeout shorter than the drain would release it early.
        async with engine.connect() as lock_connection:
            await lock_connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": DRAIN_LOCK}
            )
            async with get_db_context() as db:
                if await legacy_broker_drained(db):
                    return
            await _drain(url)
    except Exception:
        logger.exception("Could not drain the legacy broker; the next start tries again")
