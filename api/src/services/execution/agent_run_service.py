"""Agent run enqueue and result waiting.

PostgreSQL is authoritative for every admitted AgentRun: the immutable
execution snapshot, invocation input, output schema, caller context, and
correlation are persisted in the same transaction that admits the row.
The RabbitMQ message is only a nudge carrying the run ID. Redis carries
no required execution state; it only supports synchronous waiter
notification via the result key the consumer always publishes.
"""
import json
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.database import get_session_factory
from src.core.log_safety import log_safe
from src.jobs.rabbitmq import publish_message
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent

logger = logging.getLogger(__name__)

QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"


async def enqueue_agent_run(
    agent_id: str | None,
    trigger_type: str,
    input_data: dict | None = None,
    *,
    trigger_source: str | None = None,
    output_schema: dict | None = None,
    org_id: str | None = None,
    caller_user_id: str | None = None,
    caller_email: str | None = None,
    caller_name: str | None = None,
    caller_is_superuser: bool = False,
    caller_is_external: bool = False,
    caller_is_provider_org: bool = False,
    caller_roles: list[str] | None = None,
    caller_context: dict | None = None,
    correlation: dict | None = None,
    event_delivery_id: str | None = None,
    conversation_id: str | None = None,
    sync: bool = False,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    root_run_id: str | None = None,
    before_queue_publish: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Persist and enqueue an agent run for worker processing.

    The database row — including the immutable execution snapshot for
    agent-backed non-chat runs — is committed before the queue nudge is
    published, so a returned run ID is immediately queryable and executable
    even if Redis loses everything. If RabbitMQ rejects the nudge, the row
    stays queued with a recoverable delivery error instead of being marked
    terminal; a later scheduler pass requeues it.
    """
    if run_id is None:
        run_id = str(uuid4())

    run_uuid = UUID(run_id)
    session_factory = get_session_factory()
    async with session_factory() as db:
        agent: Agent | None = None
        if agent_id is not None:
            agent = (
                await db.execute(
                    select(Agent)
                    .options(
                        selectinload(Agent.tools),
                        selectinload(Agent.delegated_agents),
                        selectinload(Agent.roles),
                    )
                    .where(Agent.id == UUID(agent_id))
                )
            ).scalar_one_or_none()

        execution_snapshot: dict | None = None
        if agent is not None and trigger_type != "chat":
            from src.services.agent_runtime.execution_snapshot import (
                snapshot_agent,
            )

            execution_snapshot = await snapshot_agent(
                db,
                agent,
                caller_user_id=(
                    UUID(str(caller_user_id)) if caller_user_id else None
                ),
            )

        db.add(
            AgentRun(
                id=run_uuid,
                agent_id=UUID(agent_id) if agent_id else None,
                trigger_type=trigger_type,
                trigger_source=trigger_source,
                event_delivery_id=(
                    UUID(event_delivery_id) if event_delivery_id else None
                ),
                conversation_id=UUID(conversation_id) if conversation_id else None,
                input=input_data,
                output_schema=output_schema,
                status="queued",
                org_id=UUID(org_id) if org_id else None,
                caller_user_id=caller_user_id,
                caller_email=caller_email,
                caller_name=caller_name,
                caller_context=caller_context,
                correlation=correlation,
                execution_snapshot=execution_snapshot,
                parent_run_id=UUID(parent_run_id) if parent_run_id else None,
                # Delegation passes the parent's root explicitly; a
                # top-level run roots its own tree.
                root_run_id=UUID(root_run_id) if root_run_id else run_uuid,
            )
        )
        await db.commit()

    try:
        if before_queue_publish is not None:
            await before_queue_publish(run_id)

        # Nudge only: the consumer loads everything from PostgreSQL.
        await publish_message(QUEUE_NAME, {"run_id": run_id})
    except Exception:
        logger.exception("Failed to enqueue agent run %s", log_safe(run_id))
        async with session_factory() as db:
            queued_run = await db.get(AgentRun, run_uuid)
            if queued_run is not None and queued_run.status == "queued":
                queued_run.error = (
                    "Queue nudge delivery failed; run remains queued for reclaim"
                )
                await db.commit()
        raise

    logger.info(f"Enqueued agent run {run_id} for agent {agent_id} (trigger={trigger_type})")
    return run_id


async def wait_for_agent_run_result(run_id: str, timeout: int = 1800) -> dict | None:
    """Block until agent run completes. Used for sync SDK calls.

    The consumer publishes a result for every autonomous run, so waiters do
    not depend on any enqueue-time flag travelling through the queue.

    Uses a dedicated Redis connection with a socket_timeout that covers
    the full BLPOP wait (the default 5s socket_timeout in get_redis()
    kills the connection before the worker can push a result).
    """
    from src.config import get_settings

    result_key = f"{REDIS_PREFIX}:{run_id}:result"
    # socket_timeout must exceed the BLPOP timeout so the connection
    # stays alive for the entire blocking wait, plus a small buffer.
    client = aioredis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_timeout=float(timeout + 10),
        socket_connect_timeout=5.0,
    )
    try:
        result = await client.blpop(result_key, timeout=timeout)  # pyright: ignore[reportGeneralTypeIssues]
        if result:
            return json.loads(result[1])
        return None
    finally:
        await client.aclose()
