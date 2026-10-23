"""Agent run enqueue and result waiting."""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import redis.asyncio as aioredis

from src.core.database import get_dispatch_db_context
from src.core.log_safety import log_safe
from src.models.orm.agent_runs import AgentRun
from src.services.durable_impersonation import current_durable_impersonation
from src.services.execution.agent_run_dispatch import (
    cache_agent_context,
    hand_off_agent_run,
    stage_agent_run,
)

if TYPE_CHECKING:
    from shared.run_lineage import RunLineage

logger = logging.getLogger(__name__)

REDIS_PREFIX = "bifrost:agent_run"


def _acting_user_id(
    *,
    caller_user_id: str | None,
    lineage: RunLineage | None,
    run_as_user_id: UUID | None,
) -> UUID | None:
    """Select the durable actor once, without consulting queued context."""
    if run_as_user_id is not None:
        return run_as_user_id
    if caller_user_id is not None:
        try:
            return UUID(caller_user_id)
        except ValueError:
            pass
    return lineage.run_user_id if lineage is not None else None


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
    event_delivery_id: str | None = None,
    conversation_id: str | None = None,
    sync: bool = False,
    run_id: str | None = None,
    before_queue_publish: Callable[[str], Awaitable[None]] | None = None,
    lineage: RunLineage | None,
    run_as_user_id: UUID | None = None,
) -> str:
    """Persist and enqueue an agent run for worker processing.

    ``lineage`` says who the run is for (shared.run_lineage); the row records
    its run user. None when it can't be known.

    ``run_as_user_id`` is the authorized user the run acts as when it isn't
    the caller. The selected actor is recorded on the row; queued context is
    never an authority source.

    The run and its dispatch (holding the execution context) are committed
    before the queue message is published, so a returned run ID is
    immediately queryable and survives a Redis or RabbitMQ outage: the
    control plane republishes a lost hand-off.
    """
    if run_id is None:
        run_id = str(uuid4())

    run_uuid = UUID(run_id)
    acting_user_id = _acting_user_id(
        caller_user_id=caller_user_id,
        lineage=lineage,
        run_as_user_id=run_as_user_id,
    )
    impersonation = current_durable_impersonation()
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
            "is_superuser": caller_is_superuser,
            "is_external": caller_is_external,
            "is_provider_org": caller_is_provider_org,
            "roles": caller_roles or [],
        },
        "event_delivery_id": event_delivery_id,
        "conversation_id": conversation_id,
        "sync": sync,
        "cancelled": False,
    }
    async with get_dispatch_db_context() as db:
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
                run_user_id=lineage.run_user_id if lineage else None,
                run_as_user_id=run_as_user_id,
                acting_user_id=acting_user_id,
                **impersonation.columns(),
            )
        )
        # The AgentRun must exist before its dispatch references the subject.
        await db.flush()
        dispatch = stage_agent_run(
            db,
            run_id=run_uuid,
            context=context,
            message={
                "run_id": run_id,
                "agent_id": agent_id,
                "trigger_type": trigger_type,
                "sync": sync,
            },
        )

    # Accepted durably. Everything below is best-effort: a lost hand-off is
    # republished by the control plane's reconciler.
    await cache_agent_context(run_id, context)
    if before_queue_publish is not None:
        try:
            await before_queue_publish(run_id)
        except Exception:
            logger.warning(
                "Agent run pre-publish notice failed",
                extra={"run_id": log_safe(run_id)},
                exc_info=True,
            )
    await hand_off_agent_run(dispatch)

    logger.info(
        "Enqueued agent run %s for agent %s (trigger=%s)",
        log_safe(run_id),
        log_safe(agent_id),
        log_safe(trigger_type),
    )
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
