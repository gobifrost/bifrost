"""Deferred execution promoter.

Every 60 seconds, makes SCHEDULED executions whose ``scheduled_at`` has
matured claimable by flipping them to PENDING and staging a durable
dispatch for each (``workflow_dispatch``).

Design notes:

- The promotion UPDATE and the dispatches commit together, BEFORE the
  executors are woken. A lost wake only costs the executors' poll
  interval; the row is never stranded as PENDING.
- ``SELECT ... FOR UPDATE SKIP LOCKED`` keeps the job safe to run in
  parallel (multiple scheduler pods / APScheduler threads): each batch
  picks a disjoint set of rows.
- ``LIMIT 500`` bounds recovery bursts after an outage — if 10k rows
  matured while the promoter was down, they drain in controlled batches.
- ``user_email`` is intentionally an empty string: the Execution row does
  not persist the triggering user's email. The worker hydrates it from
  the User record keyed by ``executed_by``. ``startup=None`` for the same
  reason — startup results are per-session context and would be stale by
  the time a scheduled row matures.
- A row that acts as another user (``execution_context.run_as``) is decided
  again before it is promoted (``impersonation.recheck_run_as``). A row that
  is no longer permitted is marked Failed and never dispatched. A refusal is
  written to the audit log as a live one is (a person's enforced, a
  workflow's report-only), judged in a collector of its own.
- ``execution_context.run_as`` lives only until the row runs: the worker
  overwrites ``execution_context`` on completion. ``executed_by`` (the
  acting user) and ``started_by_user_id`` (the initiator) keep the record
  of who acted as whom.
"""
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.run_lineage import RunLineage
from shared.run_cancellation import RUN_USER_DELETED_ERROR
from src.core.database import get_db_context
from src.core.log_safety import log_safe
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.services.access_check_writer import flush_detached
from src.services.authorization.impersonation import recheck_run_as
from src.core.redis_client import PendingExecution, pending_execution_record
from src.services.execution.workflow_dispatch import (
    AcceptedRun,
    hand_off_workflow_run,
    stage_workflow_run,
)

logger = logging.getLogger(__name__)

BATCH_LIMIT = 500
# The operation a refused Run As re-check is audited under.
RECHECK_OPERATION = "scheduler.deferred_execution_promoter"


async def promote_due_executions() -> int:
    """Promote due SCHEDULED rows to PENDING and hand them to the executors.

    Returns:
        The number of runs promoted.
    """
    promoted = 0

    async with get_db_context() as db:
        result = await db.execute(
            select(Execution)
            .where(Execution.status == ExecutionStatus.SCHEDULED)
            .where(Execution.scheduled_at <= datetime.now(timezone.utc))
            .order_by(Execution.scheduled_at.asc())
            .limit(BATCH_LIMIT)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())

        if not rows:
            return 0

        rows = _cancel_rows_with_deleted_run_user(rows)
        rows = await _fail_rows_no_longer_permitted(db, rows)
        if not rows:
            await db.commit()
            return 0

        ids = [r.id for r in rows]
        await db.execute(
            update(Execution)
            .where(Execution.id.in_(ids))
            .values(status=ExecutionStatus.PENDING, started_at=None)
        )
        accepted: list[AcceptedRun] = [
            await stage_workflow_run(
                db,
                _pending_record(row),
                {
                    "execution_id": str(row.id),
                    "workflow_id": str(row.workflow_id) if row.workflow_id else None,
                    "sync": False,
                },
                record_exists=True,
            )
            for row in rows
        ]
        await db.commit()

        for run in accepted:
            await hand_off_workflow_run(run)
        promoted = len(accepted)

        logger.info(
            "deferred_execution_promoter tick complete",
            extra={"promoted": promoted},
        )
        return promoted


def _pending_record(row: Execution) -> PendingExecution:
    """The worker context for a promoted row."""
    return pending_execution_record(
        execution_id=str(row.id),
        workflow_id=str(row.workflow_id) if row.workflow_id else None,
        parameters=row.parameters or {},
        org_id=str(row.organization_id) if row.organization_id else None,
        user_id=str(row.executed_by) if row.executed_by else "",
        user_name=row.executed_by_name or "",
        user_email="",  # Not persisted on the row; worker hydrates from user record.
        form_id=str(row.form_id) if row.form_id else None,
        startup=None,  # Scheduled runs do not carry stale startup results.
        api_key_id=str(row.api_key_id) if row.api_key_id else None,
        sync=False,
        is_platform_admin=bool(
            (row.execution_context or {}).get("is_platform_admin", False)
        ),
        lineage=(
            RunLineage(
                row.run_user_id, row.started_by_user_id, row.root_execution_id
            ).bound(row.id)
            if row.run_user_id is not None
            else None
        ),
        impersonation_provenance={
            key: str(value)
            for key, value in (
                ("impersonated_by_user_id", row.impersonated_by_user_id),
                ("impersonation_session_id", row.impersonation_session_id),
            )
            if value is not None
        } or None,
    )


def _cancel_rows_with_deleted_run_user(rows: list[Execution]) -> list[Execution]:
    """Cancel scheduled rows whose preserved run user was removed.

    Deletion normally cancels the row before PostgreSQL clears
    ``run_user_id``. Some existing scheduled rows also retain their original
    run user in execution context; never promote those rows after the foreign
    key has been cleared, because the worker would otherwise fall back to a
    system caller.
    """
    promotable: list[Execution] = []
    for row in rows:
        context = row.execution_context or {}
        if row.run_user_id is not None or context.get("run_user_id") is None:
            promotable.append(row)
            continue
        row.status = ExecutionStatus.CANCELLED
        row.error_message = RUN_USER_DELETED_ERROR
        row.completed_at = datetime.now(timezone.utc)
        logger.warning(
            "deferred_execution_promoter: cancelled row whose run user was deleted",
            extra={"execution_id": log_safe(row.id)},
        )
    return promotable


async def _fail_rows_no_longer_permitted(db: AsyncSession, rows: list[Execution]) -> list[Execution]:
    """Mark Failed each row whose Run As is no longer permitted; return the others."""
    permitted: list[Execution] = []
    for row in rows:
        run_as = (row.execution_context or {}).get("run_as")
        reason = None if run_as is None else await _recheck(db, run_as)
        if reason is None:
            permitted.append(row)
            continue
        await db.execute(
            update(Execution)
            .where(Execution.id == row.id)
            .values(
                status=ExecutionStatus.FAILED,
                error_message=f"Run As is no longer permitted: {reason}",
                completed_at=datetime.now(timezone.utc),
            )
        )
        logger.warning(
            "deferred_execution_promoter: Run As no longer permitted, row failed",
            extra={"execution_id": log_safe(row.id), "reason": log_safe(reason)},
        )
    return permitted


async def _recheck(db: AsyncSession, run_as: dict[str, Any]) -> str | None:
    """``recheck_run_as``, with a refusal collected as the decision was when
    the row was scheduled (a person's for its initiator, a workflow's for
    the run that asked) and written in a session of its own: the row locks
    this promoter holds stay held until its batch commits."""
    if run_as["enforced"]:
        token = access_checks.collect_person(UUID(run_as["authorized_by"]))
    else:
        token = access_checks.collect_run(
            _optional_uuid(run_as["execution_id"]),
            _optional_uuid(run_as["run_user_id"]),
            _optional_uuid(run_as["workflow_id"]),
        )
    collector = access_checks.current() if token is not None else None
    try:
        reason = await recheck_run_as(db, run_as)
    finally:
        access_checks.stop_collecting(token)
    if collector is not None:
        await flush_detached(collector, operation=RECHECK_OPERATION, route=None)
    return reason


def _optional_uuid(value: str | None) -> UUID | None:
    return None if value is None else UUID(value)
