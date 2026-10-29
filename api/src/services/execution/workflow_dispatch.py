"""Durable acceptance of workflow and inline-code runs.

Every run is accepted as a ``PENDING`` ``Execution`` plus a ``WorkDispatch``
holding its execution context, committed together. A worker claims the run by
moving the row to ``RUNNING`` and deleting the dispatch in one transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.run_cancellation import claim_pending_run_for_dispatch
from shared.run_lineage import lineage_columns
from src.core.database import get_db_context
from src.core.redis_client import PendingExecution, get_redis_client
from src.models.enums import ExecutionStatus
from src.models.orm.execution_workers import ExecutionWorkerClaim
from src.models.orm.executions import Execution
from src.models.orm.workflows import Workflow
from src.services.durable_impersonation import pending_durable_impersonation
from src.services.memory_requests import workflow_request_bytes
from src.services.work_dispatch import hand_off, stage_dispatch, take_dispatch_payload

logger = logging.getLogger(__name__)

FAMILY = "workflow_execution"
HANDLER = "workflow.execute"
HANDLER_VERSION = 1


@dataclass(frozen=True)
class AcceptedRun:
    dispatch_id: UUID
    message: dict[str, Any]
    pending: PendingExecution


def _organization_id(org_id: str | None) -> UUID | None:
    if not org_id or org_id == "GLOBAL":
        return None
    return UUID(org_id[4:] if org_id.startswith("ORG:") else org_id)


async def _row_identity(
    db: AsyncSession, pending: PendingExecution
) -> tuple[str, UUID | None, int]:
    """Display name, owning organization and memory request for the PENDING row.

    Mirrors the worker: an org-scoped workflow's runs belong to the
    workflow's organization, others to the caller's.
    """
    caller_org = _organization_id(pending["org_id"])
    if pending["script_name"]:
        return pending["script_name"], caller_org, workflow_request_bytes(None)
    workflow_id = pending["workflow_id"]
    if workflow_id is None:
        return "workflow", caller_org, workflow_request_bytes(None)
    row = (
        await db.execute(
            select(
                Workflow.name,
                Workflow.organization_id,
                Workflow.learned_memory_bytes,
                Workflow.memory_request_mib,
            ).where(Workflow.id == UUID(workflow_id))
        )
    ).one_or_none()
    if row is None:
        # The worker records the failure when it claims the run.
        return workflow_id, caller_org, workflow_request_bytes(None)
    return (
        row.name,
        row.organization_id or caller_org,
        workflow_request_bytes(row.learned_memory_bytes, row.memory_request_mib),
    )


async def _request_for(db: AsyncSession, pending: PendingExecution) -> int:
    """The memory request of a run whose row already exists."""
    if pending["script_name"] or pending["workflow_id"] is None:
        return workflow_request_bytes(None)
    row = (
        await db.execute(
            select(Workflow.learned_memory_bytes, Workflow.memory_request_mib).where(
                Workflow.id == UUID(pending["workflow_id"])
            )
        )
    ).one_or_none()
    if row is None:
        return workflow_request_bytes(None)
    return workflow_request_bytes(row.learned_memory_bytes, row.memory_request_mib)


async def stage_workflow_run(
    db: AsyncSession,
    pending: PendingExecution,
    message: dict[str, Any],
    *,
    record_exists: bool,
) -> AcceptedRun:
    """Add the run's durable records to the caller's transaction."""
    execution_id = UUID(pending["execution_id"])
    if record_exists:
        memory_request_bytes = await _request_for(db, pending)
    else:
        workflow_name, organization_id, memory_request_bytes = await _row_identity(db, pending)
        db.add(
            Execution(
                id=execution_id,
                workflow_id=UUID(pending["workflow_id"]) if pending["workflow_id"] else None,
                workflow_name=workflow_name,
                status=ExecutionStatus.PENDING,
                parameters=pending["parameters"],
                organization_id=organization_id,
                executed_by=UUID(pending["user_id"]),
                executed_by_name=pending["user_name"],
                form_id=UUID(pending["form_id"]) if pending["form_id"] else None,
                api_key_id=UUID(pending["api_key_id"]) if pending["api_key_id"] else None,
                execution_context={"is_platform_admin": pending["is_platform_admin"]},
                **lineage_columns(pending.get("lineage")),
                **pending_durable_impersonation(
                    pending.get("impersonation_provenance")
                ).columns(),
            )
        )
    dispatch = stage_dispatch(
        db,
        family=FAMILY,
        subject_id=execution_id,
        handler=HANDLER,
        handler_version=HANDLER_VERSION,
        message=message,
        payload=dict(pending),
        memory_request_bytes=memory_request_bytes,
    )
    return AcceptedRun(
        dispatch_id=dispatch.id,
        message=dispatch.message,
        pending=pending,
    )


async def hand_off_workflow_run(accepted: AcceptedRun) -> None:
    """After commit: queue position, then wake the executors."""
    from src.services.execution.queue_tracker import publish_queue_positions

    # Sync callers wait on their private result list and cannot consume queue
    # position events.
    if not accepted.pending["sync"]:
        try:
            await publish_queue_positions()
        except Exception:
            logger.warning(
                "Could not record queue position",
                extra={"execution_id": accepted.pending["execution_id"]},
                exc_info=True,
            )
    await hand_off()


async def claim_workflow_dispatch(
    db: AsyncSession,
    *,
    dispatch_id: UUID,
    execution_id: UUID,
) -> PendingExecution | None:
    """Claim a dispatched run; None when cancellation or another delivery won."""
    claimed = await claim_pending_run_for_dispatch(db, execution_id)
    payload = await take_dispatch_payload(db, dispatch_id)
    if not claimed:
        return None
    if payload is None:
        raise RuntimeError(f"Pending execution {execution_id} has no dispatch {dispatch_id}")
    return PendingExecution(**payload)  # type: ignore[typeddict-item]


async def release_workflow_run(
    *,
    worker_id: UUID,
    message: dict[str, Any],
    payload: dict[str, Any],
    memory_request_bytes: int,
    queued_at: datetime,
) -> None:
    """Hand a claimed run back before it started: RUNNING -> PENDING.

    For an executor that claimed a run but could not fork it (memory rose in
    between). The run gets a new dispatch, keeping its place in line and its
    claim deadline, and loses this executor's claim record, so another
    executor takes it as if it had never been claimed.
    """
    execution_id = UUID(payload["execution_id"])
    async with get_db_context() as db:
        released = await db.scalar(
            update(Execution)
            .where(Execution.id == execution_id, Execution.status == ExecutionStatus.RUNNING)
            .values(status=ExecutionStatus.PENDING, started_at=None)
            .returning(Execution.id)
        )
        if released is None:
            return
        await db.execute(
            delete(ExecutionWorkerClaim).where(
                ExecutionWorkerClaim.worker_id == worker_id,
                ExecutionWorkerClaim.family == FAMILY,
                ExecutionWorkerClaim.subject_id == execution_id,
            )
        )
        stage_dispatch(
            db,
            family=FAMILY,
            subject_id=execution_id,
            handler=HANDLER,
            handler_version=HANDLER_VERSION,
            message=message,
            payload=payload,
            memory_request_bytes=memory_request_bytes,
            queued_at=queued_at,
        )
        await db.commit()
    logger.info("Handed a run back for another executor", extra={"execution_id": str(execution_id)})
    if not payload.get("sync"):
        from src.services.execution.queue_tracker import publish_queue_positions

        try:
            await publish_queue_positions()
        except Exception:
            logger.warning(
                "Could not show a handed-back run as waiting",
                extra={"execution_id": str(execution_id)},
                exc_info=True,
            )
    await hand_off()


async def lock_waiting_run(db: AsyncSession, execution_id: UUID) -> bool | None:
    """Lock the run before its dispatch (the claim's order); None if busy."""
    status = await db.scalar(
        select(Execution.status)
        .where(Execution.id == execution_id)
        .with_for_update(skip_locked=True)
    )
    if status is not None:
        return status == ExecutionStatus.PENDING
    exists = await db.scalar(select(Execution.id).where(Execution.id == execution_id))
    return None if exists is not None else False


async def fail_unclaimed_run(db: AsyncSession, execution_id: UUID, reason: str) -> None:
    await db.execute(
        update(Execution)
        .where(Execution.id == execution_id, Execution.status == ExecutionStatus.PENDING)
        .values(
            status=ExecutionStatus.FAILED,
            error_message=reason,
            completed_at=datetime.now(timezone.utc),
        )
    )


async def _notify_ended_before_start(
    execution_id: UUID,
    *,
    status: str,
    error: str,
    error_type: str,
    sync: bool,
) -> None:
    """Tell live views, the queue and any sync caller; all best-effort."""
    from src.core.pubsub import publish_execution_update

    steps = [publish_execution_update(execution_id, status, {"error": error})]
    if not sync:
        from src.services.execution.queue_tracker import publish_queue_positions

        steps.insert(0, publish_queue_positions())
    if sync:
        steps.append(
            get_redis_client().push_result(
                execution_id=str(execution_id),
                status=status,
                error=error,
                error_type=error_type,
                duration_ms=0,
            )
        )
    for step in steps:
        try:
            await step
        except Exception:
            logger.warning(
                "Could not report a run that ended before it started",
                extra={"execution_id": str(execution_id)},
                exc_info=True,
            )


async def report_unclaimed_run(
    execution_id: UUID, payload: dict[str, Any], reason: str
) -> None:
    await _notify_ended_before_start(
        execution_id,
        status=ExecutionStatus.FAILED.value,
        error=reason,
        error_type="DispatchNotClaimed",
        sync=bool(payload.get("sync")),
    )


async def notify_discarded_sync_run(db: AsyncSession, execution_id: UUID) -> None:
    """Wake a sync caller whose run ended before any worker started it.

    A duplicate delivery of a run already started must not wake the caller,
    so only runs that never started (no ``started_at``) are reported.
    """
    row = (
        await db.execute(
            select(Execution.status, Execution.started_at, Execution.error_message).where(
                Execution.id == execution_id
            )
        )
    ).one_or_none()
    if row is None or row.started_at is not None:
        return
    if row.status not in (ExecutionStatus.CANCELLED, ExecutionStatus.FAILED):
        return
    await _notify_ended_before_start(
        execution_id,
        status=row.status.value,
        error=row.error_message or "Execution was cancelled before it could start",
        error_type="CancelledBeforeStart"
        if row.status == ExecutionStatus.CANCELLED
        else "FailedBeforeStart",
        sync=True,
    )


__all__ = [
    "AcceptedRun",
    "FAMILY",
    "HANDLER",
    "HANDLER_VERSION",
    "claim_workflow_dispatch",
    "fail_unclaimed_run",
    "hand_off_workflow_run",
    "lock_waiting_run",
    "notify_discarded_sync_run",
    "release_workflow_run",
    "report_unclaimed_run",
    "stage_workflow_run",
]
