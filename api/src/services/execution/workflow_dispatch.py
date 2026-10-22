"""Durable acceptance of workflow and inline-code runs.

Every run is accepted as a ``PENDING`` ``Execution`` plus a ``WorkDispatch``
holding its execution context, committed together. A worker claims the run by
moving the row to ``RUNNING`` and deleting the dispatch in one transaction.

One-release bridge (remove in #769 stage 5): the accepting process also writes
the Redis pending record, best-effort, so a worker that predates dispatches can
still run the durable row through its ``execution_record_exists`` claim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.run_cancellation import claim_pending_run_for_dispatch
from shared.run_lineage import lineage_columns
from src.core.redis_client import PendingExecution, get_redis_client
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.workflows import Workflow
from src.services.durable_impersonation import pending_durable_impersonation
from src.services.work_dispatch import publish_dispatch, stage_dispatch, take_dispatch_payload

logger = logging.getLogger(__name__)

FAMILY = "workflow_execution"
HANDLER = "workflow.execute"
HANDLER_VERSION = 1
QUEUE_NAME = "workflow-executions"


@dataclass(frozen=True)
class AcceptedRun:
    dispatch_id: UUID
    message: dict[str, Any]
    pending: PendingExecution


def _organization_id(org_id: str | None) -> UUID | None:
    if not org_id or org_id == "GLOBAL":
        return None
    return UUID(org_id[4:] if org_id.startswith("ORG:") else org_id)


async def _workflow_name(db: AsyncSession, pending: PendingExecution) -> str:
    if pending["script_name"]:
        return pending["script_name"]
    workflow_id = pending["workflow_id"]
    if workflow_id is None:
        return "workflow"
    name = await db.scalar(select(Workflow.name).where(Workflow.id == UUID(workflow_id)))
    # The worker records the resolved name when it claims the run.
    return name or workflow_id


async def stage_workflow_run(
    db: AsyncSession,
    pending: PendingExecution,
    message: dict[str, Any],
    *,
    record_exists: bool,
) -> AcceptedRun:
    """Add the run's durable records to the caller's transaction."""
    execution_id = UUID(pending["execution_id"])
    if not record_exists:
        db.add(
            Execution(
                id=execution_id,
                workflow_id=UUID(pending["workflow_id"]) if pending["workflow_id"] else None,
                workflow_name=await _workflow_name(db, pending),
                status=ExecutionStatus.PENDING,
                parameters=pending["parameters"],
                organization_id=_organization_id(pending["org_id"]),
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
        queue=QUEUE_NAME,
        message={**message, "execution_record_exists": True},
        payload=dict(pending),
    )
    return AcceptedRun(dispatch_id=dispatch.id, message=dispatch.message, pending=pending)


async def hand_off_workflow_run(accepted: AcceptedRun) -> bool:
    """After commit: bridge cache, queue position, then fast-path publish.

    Returns whether the fast-path publish succeeded; the reconciler publishes
    the run otherwise.
    """
    from src.services.execution.queue_tracker import add_to_queue

    try:
        await get_redis_client().store_pending_execution(accepted.pending)
    except Exception:
        logger.warning(
            "Could not cache pending execution for older workers",
            extra={"execution_id": accepted.pending["execution_id"]},
            exc_info=True,
        )
    # Sync callers wait on their private result list and cannot consume queue
    # position events.
    if not accepted.pending["sync"]:
        try:
            await add_to_queue(accepted.pending["execution_id"])
        except Exception:
            logger.warning(
                "Could not record queue position",
                extra={"execution_id": accepted.pending["execution_id"]},
                exc_info=True,
            )
    return await publish_dispatch(accepted.dispatch_id, QUEUE_NAME, accepted.message)


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


async def run_is_waiting(db: AsyncSession, execution_id: UUID) -> bool:
    status = await db.scalar(select(Execution.status).where(Execution.id == execution_id))
    return status == ExecutionStatus.PENDING


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


__all__ = [
    "AcceptedRun",
    "FAMILY",
    "HANDLER",
    "HANDLER_VERSION",
    "QUEUE_NAME",
    "claim_workflow_dispatch",
    "fail_unclaimed_run",
    "hand_off_workflow_run",
    "run_is_waiting",
    "stage_workflow_run",
]
