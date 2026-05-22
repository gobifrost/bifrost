"""
Async Workflow Execution
Handles queueing of workflows via Redis + RabbitMQ

Flow:
1. API stores pending execution in Redis
2. API publishes message to RabbitMQ
3. API returns execution_id immediately (<100ms)
4. Worker reads from Redis, writes to PostgreSQL, executes

For sync execution (sync=True):
- Caller provides execution_id (already stored in Redis)
- Worker pushes result to Redis
- Caller waits on Redis BLPOP
"""

import logging
import uuid
from typing import Any

from src.core.constants import SYSTEM_USER_ID, SYSTEM_USER_EMAIL
from src.core.log_safety import log_safe
from src.core.redis_client import get_redis_client
from src.jobs.rabbitmq import publish_message
from src.sdk.context import EventContext, ExecutionContext
from src.services.execution.queue_tracker import add_to_queue

logger = logging.getLogger(__name__)

QUEUE_NAME = "workflow-executions"


async def _publish_pending(
    execution_id: str,
    workflow_id: str | None,
    parameters: dict[str, Any],
    org_id: str | None,
    user_id: str,
    user_name: str,
    user_email: str,
    form_id: str | None,
    startup: dict[str, Any] | None,
    api_key_id: str | None,
    sync: bool,
    is_platform_admin: bool,
    file_path: str | None,
    event: dict[str, Any] | None = None,
) -> None:
    """
    Write a pending-execution blob to Redis, register with the queue tracker,
    and publish the minimal dispatch message to RabbitMQ.

    Shared by the run-now path (enqueue_workflow_execution) and the deferred
    execution promoter. Callers are responsible for generating execution_id
    before calling this helper.
    """
    redis_client = get_redis_client()

    # Store pending execution in Redis (worker needs this for execution context)
    await redis_client.set_pending_execution(
        execution_id=execution_id,
        workflow_id=workflow_id,
        parameters=parameters,
        org_id=org_id,
        user_id=user_id,
        user_name=user_name,
        user_email=user_email,
        form_id=form_id,
        startup=startup,
        api_key_id=api_key_id,
        sync=sync,
        is_platform_admin=is_platform_admin,
        event=event,
    )

    # Add to queue tracking (publishes position updates to all queued executions)
    await add_to_queue(execution_id)

    # Prepare queue message (minimal - worker reads full context from Redis)
    message: dict[str, Any] = {
        "execution_id": execution_id,
        "workflow_id": workflow_id,
        "sync": sync,
    }

    # Include file_path for fast direct loading (avoids filesystem scan)
    if file_path:
        message["file_path"] = file_path

    # Enqueue message via RabbitMQ
    await publish_message(QUEUE_NAME, message)


async def enqueue_workflow_execution(
    context: ExecutionContext,
    workflow_id: str,
    parameters: dict[str, Any],
    form_id: str | None = None,
    execution_id: str | None = None,
    sync: bool = False,
    api_key_id: str | None = None,
    file_path: str | None = None,
) -> str:
    """
    Enqueue a workflow for async execution.

    Stores pending execution in Redis, publishes to RabbitMQ,
    and returns execution ID immediately (<100ms target).

    Args:
        context: Request context with org scope and user info
        workflow_id: UUID of workflow to execute (from database)
        parameters: Workflow parameters
        form_id: Optional form ID if triggered by form
        execution_id: Optional pre-generated execution ID (for sync execution)
        sync: If True, worker will push result to Redis for caller to BLPOP
        api_key_id: Optional workflow ID whose API key triggered this execution
        file_path: Optional file path (for fast direct loading, avoids filesystem scan)

    Returns:
        execution_id: UUID of the queued execution
    """
    # Generate or use provided execution ID
    if execution_id is None:
        execution_id = str(uuid.uuid4())

    # Serialize event context for cross-process transit. EventContext is a
    # dataclass with primitive fields, so dict serialization is lossless and
    # JSON-safe for Redis storage.
    event_payload: dict[str, Any] | None = None
    if context.event is not None:
        import dataclasses
        event_payload = dataclasses.asdict(context.event)

    await _publish_pending(
        execution_id=execution_id,
        workflow_id=workflow_id,
        parameters=parameters,
        org_id=context.org_id,
        user_id=context.user_id,
        user_name=context.name,
        user_email=context.email,
        form_id=form_id,
        startup=context.startup,
        api_key_id=api_key_id,
        sync=sync,
        is_platform_admin=context.is_platform_admin,
        file_path=file_path,
        event=event_payload,
    )

    logger.info(
        f"Enqueued async workflow execution: {workflow_id}",
        extra={
            "execution_id": execution_id,
            "workflow_id": workflow_id,
            "org_id": context.org_id
        }
    )

    return execution_id


async def enqueue_code_execution(
    context: ExecutionContext,
    script_name: str,
    code_base64: str,
    parameters: dict[str, Any],
    execution_id: str | None = None,
    sync: bool = False,
) -> str:
    """
    Enqueue inline code for async execution.

    Stores pending execution in Redis, publishes to RabbitMQ,
    and returns execution ID immediately (<100ms target).

    Args:
        context: Request context with org scope and user info
        script_name: Name/identifier for the script
        code_base64: Base64-encoded Python code
        parameters: Script parameters
        execution_id: Optional pre-generated execution ID (for sync execution)
        sync: If True, worker will push result to Redis for caller to BLPOP

    Returns:
        execution_id: UUID of the queued execution
    """
    redis_client = get_redis_client()

    # Generate or use provided execution ID
    if execution_id is None:
        execution_id = str(uuid.uuid4())

    # Store pending execution in Redis (worker needs this for execution context)
    await redis_client.set_pending_execution(
        execution_id=execution_id,
        workflow_id=None,  # No workflow ID for inline code
        script_name=script_name,
        parameters=parameters,
        org_id=context.org_id,
        user_id=context.user_id,
        user_name=context.name,
        user_email=context.email,
        form_id=None,
    )

    # Add to queue tracking
    await add_to_queue(execution_id)

    # Prepare queue message with code
    message = {
        "execution_id": execution_id,
        "code": code_base64,
        "script_name": script_name,
        "sync": sync,
    }

    # Enqueue message via RabbitMQ
    await publish_message(QUEUE_NAME, message)

    logger.info(
        f"Enqueued async code execution: {log_safe(script_name)}",
        extra={
            "execution_id": execution_id,
            "script_name": log_safe(script_name),
            "org_id": context.org_id
        }
    )

    return execution_id


async def enqueue_system_workflow_execution(
    workflow_id: str,
    parameters: dict[str, Any],
    source: str,
    org_id: str | None = None,
    event: EventContext | None = None,
) -> str:
    """
    Enqueue a system-triggered workflow execution.

    Handles execution_id generation internally - callers don't need to pre-generate.
    Uses the system user for executions not triggered by a real user
    (webhooks, schedules, topic events).

    Args:
        workflow_id: UUID of workflow to execute
        parameters: Workflow parameters
        source: Display name for what triggered this (e.g., "Event System", "Scheduled Execution")
        org_id: Optional organization scope (UUID string, not "ORG:" prefixed)
        event: Optional EventContext populated for event-triggered executions

    Returns:
        execution_id: UUID string of the queued execution
    """
    # Generate execution_id once - used for both context and Redis
    execution_id = str(uuid.uuid4())

    from src.config import get_settings

    context = ExecutionContext(
        user_id=SYSTEM_USER_ID,
        email=SYSTEM_USER_EMAIL,
        name=source,
        scope=f"ORG:{org_id}" if org_id else "GLOBAL",
        organization=None,
        is_platform_admin=True,
        is_function_key=False,
        execution_id=execution_id,
        workflow_name="",  # Will be set by worker when loading workflow
        public_url=get_settings().public_url,
        event=event,
    )

    return await enqueue_workflow_execution(
        context=context,
        workflow_id=workflow_id,
        parameters=parameters,
        execution_id=execution_id,  # Pass explicitly to avoid double generation
    )
