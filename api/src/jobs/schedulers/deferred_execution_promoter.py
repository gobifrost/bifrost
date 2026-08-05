"""Deferred execution promoter.

Every 60 seconds, moves SCHEDULED executions whose ``scheduled_at`` has
matured onto the RabbitMQ workflow-executions queue by flipping them to
PENDING and calling the shared ``_publish_pending`` helper.

Design notes:

- The promotion UPDATE is committed BEFORE the per-row publish loop, so
  PENDING is the authoritative record that "this run belongs to RabbitMQ
  now". If the broker publish fails we best-effort revert the row back to
  SCHEDULED so the next tick retries.
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
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from src.core.database import get_db_context
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.services.execution.async_executor import _publish_pending

logger = logging.getLogger(__name__)

BATCH_LIMIT = 500


async def promote_due_executions() -> tuple[int, int]:
    """Promote due SCHEDULED rows to PENDING and publish them.

    Returns:
        Tuple of (promoted_count, publish_failures).
    """
    promoted = 0
    failures = 0

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
            return 0, 0

        ids = [r.id for r in rows]
        await db.execute(
            update(Execution)
            .where(Execution.id.in_(ids))
            .values(status=ExecutionStatus.PENDING, started_at=None)
        )
        await db.commit()

        for row in rows:
            try:
                await _publish_pending(
                    execution_id=str(row.id),
                    workflow_id=str(row.workflow_id) if row.workflow_id else None,
                    parameters=row.parameters or {},
                    org_id=str(row.organization_id) if row.organization_id else None,
                    user_id=str(row.executed_by) if row.executed_by else "",
                    user_name=row.executed_by_name or "",
                    user_email="",  # Not persisted on the row; worker hydrates from user record.
                    form_id=str(row.form_id) if row.form_id else None,
                    startup=None,  # Scheduled runs do not carry stale startup results.
                    form_inputs={},
                    embed={},
                    api_key_id=str(row.api_key_id) if row.api_key_id else None,
                    sync=False,
                    is_platform_admin=bool(
                        (row.execution_context or {}).get("is_platform_admin", False)
                    ),
                    file_path=None,
                )
                promoted += 1
            except Exception:
                failures += 1
                logger.exception(
                    "deferred_execution_promoter: publish failed, reverting row",
                    extra={"execution_id": str(row.id)},
                )
                await db.execute(
                    update(Execution)
                    .where(Execution.id == row.id)
                    .where(Execution.status == ExecutionStatus.PENDING)
                    .values(status=ExecutionStatus.SCHEDULED)
                )
                await db.commit()

        logger.info(
            "deferred_execution_promoter tick complete",
            extra={"promoted": promoted, "failures": failures},
        )
        return promoted, failures
