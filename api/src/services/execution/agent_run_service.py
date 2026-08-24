"""Agent run enqueue and result waiting."""
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import redis.asyncio as aioredis

from src.core.cache.redis_client import get_redis
from src.core.database import get_session_factory
from src.core.log_safety import log_safe
from src.jobs.rabbitmq import publish_message
from src.models.orm.agent_runs import AgentRun

logger = logging.getLogger(__name__)

QUEUE_NAME = "agent-runs"
REDIS_PREFIX = "bifrost:agent_run"


async def enqueue_agent_run(
    agent_id: str,
    trigger_type: str,
    input_data: dict | None = None,
    *,
    trigger_source: str | None = None,
    output_schema: dict | None = None,
    org_id: str | None = None,
    caller_user_id: str | None = None,
    caller_email: str | None = None,
    caller_name: str | None = None,
    event_delivery_id: str | None = None,
    sync: bool = False,
    run_id: str | None = None,
) -> str:
    """Persist and enqueue an agent run for worker processing.

    The database row is committed before the queue message is published so a
    returned run ID is immediately queryable. If Redis or RabbitMQ rejects the
    enqueue operation, the durable row is marked failed and the exception is
    propagated to the caller.
    """
    if run_id is None:
        run_id = str(uuid4())

    run_uuid = UUID(run_id)
    session_factory = get_session_factory()
    async with session_factory() as db:
        db.add(
            AgentRun(
                id=run_uuid,
                agent_id=UUID(agent_id),
                trigger_type=trigger_type,
                trigger_source=trigger_source,
                event_delivery_id=(
                    UUID(event_delivery_id) if event_delivery_id else None
                ),
                input=input_data,
                output_schema=output_schema,
                status="queued",
                org_id=UUID(org_id) if org_id else None,
                caller_user_id=caller_user_id,
                caller_email=caller_email,
                caller_name=caller_name,
            )
        )
        await db.commit()

    context = {
        "run_id": run_id,
        "agent_id": agent_id,
        "trigger_type": trigger_type,
        "trigger_source": trigger_source,
        "input": input_data,
        "output_schema": output_schema,
        "org_id": org_id,
        "caller": {
            "user_id": caller_user_id,
            "email": caller_email,
            "name": caller_name,
            "organization_id": org_id,
        },
        "event_delivery_id": event_delivery_id,
        "sync": sync,
        "cancelled": False,
    }

    redis_key = f"{REDIS_PREFIX}:{run_id}:context"
    try:
        # Store full context in Redis, then publish a lightweight queue message.
        async with get_redis() as redis:
            await redis.set(redis_key, json.dumps(context), ex=3600)

        message = {
            "run_id": run_id,
            "agent_id": agent_id,
            "trigger_type": trigger_type,
            "sync": sync,
        }
        await publish_message(QUEUE_NAME, message)
    except Exception:
        logger.exception("Failed to enqueue agent run %s", log_safe(run_id))
        async with session_factory() as db:
            queued_run = await db.get(AgentRun, run_uuid)
            if queued_run is not None and queued_run.status == "queued":
                queued_run.status = "failed"
                queued_run.error = "Agent run could not be queued"
                queued_run.completed_at = datetime.now(timezone.utc)
                await db.commit()
        try:
            async with get_redis() as redis:
                await redis.delete(redis_key)
        except Exception as cleanup_error:
            logger.debug(
                "Failed to clean up enqueue context for %s: %s",
                log_safe(run_id),
                log_safe(cleanup_error),
            )
        raise

    logger.info(f"Enqueued agent run {run_id} for agent {agent_id} (trigger={trigger_type})")
    return run_id


async def wait_for_agent_run_result(run_id: str, timeout: int = 1800) -> dict | None:
    """Block until agent run completes. Used for sync SDK calls.

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
