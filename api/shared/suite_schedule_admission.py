"""Scheduled evaluation-suite admission for recurring triggers.

Calls the same shared matrix orchestration as HTTP batch admission, so
scheduled and on-demand matrices share authorization, snapshots, dedupe,
quotas, and membership. The fire receipt records the matrix id; its
platform job id is the first admitted cell's job (audit witness only —
the matrix holds every cell). No routes, CLI, or executor changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.evaluation_matrix_admission import (
    MatrixAdmissionError,
    admit_matrix_batch,
    lock_suite_or_404,
)
from shared.recurring_trigger_registry import (
    TriggerDefinitionError,
    TriggerRequesterError,
    load_trigger_requester,
    validate_trigger_definition,
)
from src.models.orm.agent_evaluations import (
    AgentEvaluationExecution,
    AgentEvaluationMatrix,
)
from src.models.orm.recurring_triggers import (
    RecurringPlatformJobTrigger,
    RecurringTriggerFire,
)

_ACTIVE_CELL_STATUSES = ("queued", "running", "waiting")


async def admit_scheduled_suite(
    db: AsyncSession,
    *,
    trigger: RecurringPlatformJobTrigger,
    fire: RecurringTriggerFire,
    scheduled_for: datetime,
) -> RecurringTriggerFire:
    """Admit one claimed suite fire; marks it admitted/skipped/failed.

    Flushes domain rows; the caller commits. Domain conditions become
    fire receipts, never partial admission.
    """

    def _receipt(status: str, reason: str | None) -> RecurringTriggerFire:
        fire.status = status
        fire.reason = (reason or "")[:120] or None
        fire.updated_at = datetime.now(timezone.utc)
        return fire

    try:
        params = validate_trigger_definition(
            operation_type=trigger.operation_type,
            operation_params=trigger.operation_params,
            cron_expression=trigger.cron_expression,
            timezone_name=trigger.timezone,
            overlap_policy=trigger.overlap_policy,
        )
    except TriggerDefinitionError as exc:
        return _receipt("failed", f"invalid_definition:{exc.code}")

    if not trigger.enabled:
        return _receipt("skipped", "trigger_disabled")

    try:
        principal = await load_trigger_requester(db, trigger.requested_by_user_id)
    except TriggerRequesterError as exc:
        return _receipt("skipped", exc.code)
    if not principal.is_superuser and trigger.org_id != principal.organization_id:
        return _receipt("skipped", "requester_unauthorized")

    try:
        suite = await lock_suite_or_404(
            db, principal, UUID(str(params["suite_id"]))
        )
    except MatrixAdmissionError:
        return _receipt("skipped", "missing_target")
    if suite.org_id != trigger.org_id:
        return _receipt("skipped", "missing_target")
    if suite.status != "published":
        return _receipt("skipped", "suite_not_published")

    try:
        async with db.begin_nested():
            matrix, admitted, _published_jobs, _total_runs = await admit_matrix_batch(
                db,
                principal,
                suite_id=suite.id,
                candidate_ids=[UUID(item) for item in params["candidate_ids"]],
                profile_ids=[UUID(item) for item in params["profile_ids"]],
                repetitions_override=params["repetitions_override"],
            )
    except MatrixAdmissionError as exc:
        # Savepoint rolls back the flushed matrix/executions/results/jobs,
        # so a mid-batch failure leaves no partial work behind the receipt.
        return _receipt("skipped", exc.code)

    if not admitted:
        return _receipt("skipped", "no_cells")

    first_job_id = admitted[0][1].id
    fire.status = "admitted"
    fire.reason = None
    fire.platform_job_id = first_job_id
    fire.domain_run_id = matrix.id
    fire.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return fire


async def latest_admitted_matrix_active(
    db: AsyncSession, *, trigger_id: UUID
) -> bool:
    """True when the latest admitted matrix still has an active cell."""
    fire_id_row = (
        await db.execute(
            select(RecurringTriggerFire.id)
            .where(
                RecurringTriggerFire.trigger_id == trigger_id,
                RecurringTriggerFire.status == "admitted",
            )
            .order_by(
                RecurringTriggerFire.scheduled_for.desc(),
                RecurringTriggerFire.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if fire_id_row is None:
        return False
    fire = await db.get(RecurringTriggerFire, fire_id_row)
    if fire is None or fire.domain_run_id is None:
        return False
    matrix = await db.get(AgentEvaluationMatrix, fire.domain_run_id)
    if matrix is None:
        return False
    member_ids = [UUID(value) for value in (matrix.cell_execution_ids or [])]
    if not member_ids:
        return False
    active = (
        await db.execute(
            select(AgentEvaluationExecution.id)
            .where(
                AgentEvaluationExecution.id.in_(member_ids),
                AgentEvaluationExecution.status.in_(_ACTIVE_CELL_STATUSES),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return active is not None
