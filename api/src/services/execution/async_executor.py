"""
Async Workflow Execution
Handles durable acceptance and queueing of workflow and inline-code runs.

Flow (see ``workflow_dispatch``):
1. API commits a PENDING Execution and a WorkDispatch holding its context
2. API wakes the executors (a hint; they also poll)
3. API returns execution_id immediately
4. An executor claims PENDING -> RUNNING, taking the context from the dispatch

For sync execution (sync=True):
- Caller provides execution_id
- The executor pushes the result to Redis
- Caller waits on Redis BLPOP
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.core.database import get_dispatch_db_context
from src.core.log_safety import log_safe
from src.core.redis_client import PendingExecution, pending_execution_record
from src.sdk.context import EventContext, ExecutionContext
from src.services.durable_impersonation import current_durable_impersonation
from src.services.execution.workflow_dispatch import (
    AcceptedRun,
    hand_off_workflow_run,
    stage_workflow_run,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from shared.run_lineage import RunLineage

logger = logging.getLogger(__name__)

async def _accept(pending: PendingExecution, message: dict[str, Any]) -> None:
    """Commit the run durably, then hand it to the broker."""
    async with get_dispatch_db_context() as db:
        accepted = await stage_workflow_run(db, pending, message, record_exists=False)
        await db.commit()
    await hand_off_workflow_run(accepted)


def _pending_run(
    execution_id: str,
    workflow_id: str | None,
    parameters: dict[str, Any],
    org_id: str | None,
    user_id: str,
    user_name: str,
    user_email: str,
    form_id: str | None,
    startup: Any | None,
    form_inputs: dict[str, Any],
    embed: dict[str, Any],
    api_key_id: str | None,
    sync: bool,
    is_platform_admin: bool,
    file_path: str | None,
    event: dict[str, Any] | None = None,
    dispatch_metadata: dict[str, Any] | None = None,
    artifact_workspace_id: str | None = None,
    *,
    lineage: dict[str, str] | None,
    impersonation_provenance: dict[str, str] | None = None,
) -> tuple[PendingExecution, dict[str, Any]]:
    """The run's durable context and its dispatch message."""
    pending = pending_execution_record(
        execution_id=execution_id,
        workflow_id=workflow_id,
        parameters=parameters,
        org_id=org_id,
        user_id=user_id,
        user_name=user_name,
        user_email=user_email,
        form_id=form_id,
        startup=startup,
        form_inputs=form_inputs,
        embed=embed,
        api_key_id=api_key_id,
        sync=sync,
        is_platform_admin=is_platform_admin,
        event=event,
        artifact_workspace_id=artifact_workspace_id,
        lineage=lineage,
        impersonation_provenance=(
            current_durable_impersonation().pending_value()
            if impersonation_provenance is None
            else impersonation_provenance
        ),
    )
    message: dict[str, Any] = {
        "execution_id": execution_id,
        "workflow_id": workflow_id,
        "sync": sync,
    }
    if dispatch_metadata is not None:
        message["dispatch_metadata"] = dispatch_metadata
    # Include file_path for fast direct loading (avoids filesystem scan)
    if file_path:
        message["file_path"] = file_path
    return pending, message


async def _publish_pending(**run: Any) -> None:
    """Durably accept a workflow run and dispatch it to a worker."""
    await _accept(*_pending_run(**run))


def _context_fields(context: ExecutionContext) -> dict[str, Any]:
    """The run-context fields a pending run carries for its worker."""
    # EventContext is a dataclass with primitive fields, so dict
    # serialization is lossless and JSON-safe for transit.
    event_payload: dict[str, Any] | None = None
    if context.event is not None:
        import dataclasses

        event_payload = dataclasses.asdict(context.event)
    return {
        "org_id": context.org_id,
        "user_id": context.user_id,
        "user_name": context.name,
        "user_email": context.email,
        "startup": context.startup,
        "form_inputs": context.form_inputs,
        "embed": context.embed,
        "is_platform_admin": context.is_platform_admin,
        "event": event_payload,
        "artifact_workspace_id": context.artifact_workspace_id,
    }


async def enqueue_workflow_execution(
    context: ExecutionContext,
    workflow_id: str,
    parameters: dict[str, Any],
    form_id: str | None = None,
    execution_id: str | None = None,
    sync: bool = False,
    api_key_id: str | None = None,
    file_path: str | None = None,
    dispatch_metadata: dict[str, Any] | None = None,
    *,
    lineage: RunLineage | None,
) -> str:
    """
    Enqueue a workflow for async execution.

    Commits the run and its dispatch, wakes the executors, and returns the
    execution ID immediately (<100ms target).

    Args:
        context: Request context with org scope and user info
        workflow_id: UUID of workflow to execute (from database)
        parameters: Workflow parameters
        form_id: Optional form ID if triggered by form
        execution_id: Optional pre-generated execution ID (for sync execution)
        sync: If True, worker will push result to Redis for caller to BLPOP
        api_key_id: Optional workflow ID whose API key triggered this execution
        file_path: Optional file path (for fast direct loading, avoids filesystem scan)
        lineage: Who the run is for (shared.run_lineage); None when unknown

    Returns:
        execution_id: UUID of the queued execution
    """
    # Generate or use provided execution ID
    if execution_id is None:
        execution_id = str(uuid.uuid4())

    await _publish_pending(
        execution_id=execution_id,
        workflow_id=workflow_id,
        parameters=parameters,
        form_id=form_id,
        api_key_id=api_key_id,
        sync=sync,
        file_path=file_path,
        dispatch_metadata=dispatch_metadata,
        lineage=lineage.bound(execution_id) if lineage else None,
        **_context_fields(context),
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
    *,
    lineage: RunLineage | None,
) -> str:
    """
    Enqueue inline code for async execution.

    Commits the run and its dispatch, wakes the executors, and returns the
    execution ID immediately (<100ms target).

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
    # Generate or use provided execution ID
    if execution_id is None:
        execution_id = str(uuid.uuid4())

    pending = pending_execution_record(
        execution_id=execution_id,
        workflow_id=None,  # No workflow ID for inline code
        script_name=script_name,
        parameters=parameters,
        org_id=context.org_id,
        user_id=context.user_id,
        user_name=context.name,
        user_email=context.email,
        form_id=None,
        startup=context.startup,
        form_inputs=context.form_inputs,
        embed=context.embed,
        sync=sync,
        is_platform_admin=context.is_platform_admin,
        artifact_workspace_id=context.artifact_workspace_id,
        lineage=lineage.bound(execution_id) if lineage else None,
        impersonation_provenance=current_durable_impersonation().pending_value(),
    )
    await _accept(
        pending,
        {
            "execution_id": execution_id,
            "code": code_base64,
            "script_name": script_name,
            "sync": sync,
        },
    )

    logger.info(
        f"Enqueued async code execution: {log_safe(script_name)}",
        extra={
            "execution_id": execution_id,
            "script_name": log_safe(script_name),
            "org_id": context.org_id
        }
    )

    return execution_id


async def stage_system_workflow_execution(
    db: AsyncSession,
    workflow_id: str,
    parameters: dict[str, Any],
    org_id: str | None = None,
    event: EventContext | None = None,
    *,
    lineage: RunLineage,
) -> AcceptedRun:
    """Stage a system-triggered workflow run in the caller's transaction.

    Runs as the selected workflow identity for unattended effects (webhooks,
    schedules, topic events). The run commits with the caller's own record
    of it (an event delivery); hand it off with ``hand_off_workflow_run``
    after that commit.

    Args:
        db: The caller's session; nothing is committed here
        workflow_id: UUID of workflow to execute
        parameters: Workflow parameters
        org_id: Optional organization scope (UUID string, not "ORG:" prefixed)
        event: Optional EventContext populated for event-triggered executions
        lineage: The identity the run is for (shared.run_lineage.unattended_lineage)
    """
    from shared.unattended_execution import create_unattended_context

    execution_id = str(uuid.uuid4())
    context = await create_unattended_context(
        db, lineage=lineage, execution_id=execution_id, org_id=org_id, event=event,
    )
    pending, message = _pending_run(
        execution_id=execution_id,
        workflow_id=workflow_id,
        parameters=parameters,
        form_id=None,
        api_key_id=None,
        sync=False,
        file_path=None,
        lineage=lineage.bound(execution_id),
        **_context_fields(context),
    )
    return await stage_workflow_run(db, pending, message, record_exists=False)
