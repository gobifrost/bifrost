"""Shared recurring PlatformJob trigger processor.

Runs on the elected scheduler leader every minute. For each enabled trigger
with a due cron occurrence, claims the (trigger, occurrence) fire fence and
calls the registered operation admission. PlatformJob remains the only
lifecycle; fire rows are audit receipts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.core.database import get_db_context
from src.jobs.schedulers.cron_scheduler import _latest_due_run_utc
from src.models.orm.recurring_triggers import RecurringPlatformJobTrigger
from shared.review_schedule_admission import (
    admit_scheduled_review,
    claim_fire,
    latest_admitted_job_active,
)
from shared.recurring_trigger_registry import (
    OPERATION_AGENT_EVALUATION_SUITE,
    OPERATION_AGENT_REVIEW,
)

logger = logging.getLogger(__name__)


async def process_recurring_platform_job_triggers() -> dict[str, Any]:
    """Evaluate due trigger occurrences and admit scheduled quality work."""
    results: dict[str, Any] = {
        "total_triggers": 0,
        "due": 0,
        "admitted": 0,
        "skipped": 0,
        "failed": 0,
        "duplicates": 0,
        "errors": [],
    }
    now_utc = datetime.now(timezone.utc)
    try:
        async with get_db_context() as db:
            triggers = (
                (
                    await db.execute(
                        select(RecurringPlatformJobTrigger).where(
                            RecurringPlatformJobTrigger.enabled.is_(True)
                        )
                    )
                )
                .scalars()
                .all()
            )
            results["total_triggers"] = len(triggers)
            for trigger in triggers:
                try:
                    scheduled_for = _latest_due_run_utc(
                        trigger.cron_expression, now_utc, trigger.timezone
                    )
                except Exception as exc:
                    logger.warning(
                        "recurring trigger %s has invalid schedule: %s",
                        trigger.id,
                        exc,
                    )
                    results["errors"].append(
                        {"trigger_id": str(trigger.id), "error": str(exc)}
                    )
                    continue
                if scheduled_for is None:
                    continue
                results["due"] += 1
                fire = await claim_fire(
                    db, trigger_id=trigger.id, scheduled_for=scheduled_for
                )
                if fire.status != "claimed":
                    results["duplicates"] += 1
                    continue
                if trigger.operation_type == OPERATION_AGENT_REVIEW:
                    if await latest_admitted_job_active(db, trigger_id=trigger.id):
                        fire.status = "skipped"
                        fire.reason = "overlap"
                        fire.updated_at = datetime.now(timezone.utc)
                        await db.flush()
                        await db.commit()
                        results["skipped"] += 1
                        continue
                    fire = await admit_scheduled_review(
                        db,
                        trigger=trigger,
                        fire=fire,
                        scheduled_for=scheduled_for,
                    )
                elif trigger.operation_type == OPERATION_AGENT_EVALUATION_SUITE:
                    from shared.suite_schedule_admission import (
                        admit_scheduled_suite,
                        latest_admitted_matrix_active,
                    )

                    if await latest_admitted_matrix_active(
                        db, trigger_id=trigger.id
                    ):
                        fire.status = "skipped"
                        fire.reason = "overlap"
                        fire.updated_at = datetime.now(timezone.utc)
                        await db.flush()
                        await db.commit()
                        results["skipped"] += 1
                        continue
                    fire = await admit_scheduled_suite(
                        db,
                        trigger=trigger,
                        fire=fire,
                        scheduled_for=scheduled_for,
                    )
                else:
                    fire.status = "failed"
                    fire.reason = "operation_not_registered"
                    await db.flush()
                    await db.commit()
                    results["failed"] += 1
                    continue
                await db.commit()
                if fire.status == "admitted":
                    results["admitted"] += 1
                elif fire.status == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1
    except Exception as exc:
        logger.error("recurring trigger processor failed: %s", exc, exc_info=True)
        results["errors"].append({"error": str(exc)})
    return results
