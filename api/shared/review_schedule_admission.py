"""Scheduled review admission for recurring triggers.

Reuses the on-demand evidence/profile/fingerprint/enqueue helpers so scheduled
and HTTP admissions share request identity and accounting. The scheduler owns
due evaluation and the fire fence; this module owns selection + admission for
one claimed fire. No routes, CLI, or executor changes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.agent_reviews import (
    MAX_REVIEW_RUNS,
    AgentReviewServiceError,
    assert_review_sources_readable,
    authorize_review_agent,
    build_review_evidence_input,
    freeze_review_profile_snapshot,
    profile_snapshot_to_dict,
    review_request_fingerprint,
)
from shared.models import AgentReviewJobPayload
from shared.recurring_trigger_registry import (
    TriggerRequesterError,
    load_trigger_requester,
    validate_trigger_definition,
)
from src.core.principal import UserPrincipal
from src.models.orm.agent_reviews import (
    AgentReviewDefinition,
    AgentReviewRun,
    AgentReviewVersion,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.recurring_triggers import (
    RecurringPlatformJobTrigger,
    RecurringTriggerFire,
)
from src.services.agent_runtime.types import TERMINAL_STATUSES
from src.services.platform_jobs import enqueue_platform_job


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _hash(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


async def _requester_principal(db: AsyncSession, user_id: UUID) -> UserPrincipal:
    try:
        return await load_trigger_requester(db, user_id)
    except TriggerRequesterError as exc:
        raise AgentReviewServiceError(exc.code)


async def _latest_version(db: AsyncSession, review: AgentReviewDefinition) -> AgentReviewVersion:
    version = (
        await db.execute(
            select(AgentReviewVersion).where(
                AgentReviewVersion.review_id == review.id,
                AgentReviewVersion.version == review.latest_version,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise AgentReviewServiceError("review_version_missing")
    return version


async def select_scheduled_runs(
    db: AsyncSession,
    *,
    review: AgentReviewDefinition,
    scheduled_for: datetime,
    lookback_days: int,
) -> list[UUID]:
    """Select eligible terminal production runs inside the due window.

    Every TERMINAL_STATUSES state is eligible, not only successful runs:
    failed/timeout runs can hold the problems a review should inspect. The
    evidence builder still fail-closes per fire on unreadable input.
    """
    window_start = scheduled_for - timedelta(days=lookback_days)
    rows = (
        (
            await db.execute(
                select(AgentRun.id)
                .where(
                    AgentRun.agent_id == review.agent_id,
                    AgentRun.org_id.is_not_distinct_from(review.org_id),
                    AgentRun.trigger_type != "evaluation_synthetic",
                    AgentRun.status.in_(TERMINAL_STATUSES),
                    AgentRun.completed_at.is_not(None),
                    AgentRun.completed_at >= window_start,
                    AgentRun.completed_at < scheduled_for,
                )
                .order_by(AgentRun.completed_at.desc(), AgentRun.id.desc())
                .limit(MAX_REVIEW_RUNS)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def admit_scheduled_review(
    db: AsyncSession,
    *,
    trigger: RecurringPlatformJobTrigger,
    fire: RecurringTriggerFire,
    scheduled_for: datetime,
) -> RecurringTriggerFire:
    """Admit one claimed fire; marks the fire admitted/skipped/failed.

    Flushes domain rows; the caller commits. Never raises for domain
    conditions — they become fire receipts. Import/usage errors in the
    trigger definition also become receipts, never partial admission.
    """
    from src.jobs.platform.agent_review import AGENT_REVIEW_DEFINITION

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
    except Exception as exc:
        code = getattr(exc, "code", "invalid_definition")
        return _receipt("failed", f"invalid_definition:{code}")

    if not trigger.enabled:
        return _receipt("skipped", "trigger_disabled")

    try:
        principal = await _requester_principal(db, trigger.requested_by_user_id)
    # Tenant isolation for org users; superusers are governed by the same
    # authorize_review_agent check as HTTP admission. System accounts can
    # never own a schedule (rejected in _requester_principal).
    except AgentReviewServiceError:
        return _receipt("skipped", "requester_unauthorized")
    if not principal.is_superuser and trigger.org_id != principal.organization_id:
        return _receipt("skipped", "requester_unauthorized")

    review = await db.get(AgentReviewDefinition, UUID(str(params["review_id"])))
    if review is None:
        return _receipt("skipped", "missing_target")
    if review.org_id != trigger.org_id:
        return _receipt("skipped", "missing_target")
    if review.status != "active":
        return _receipt("skipped", "review_disabled")

    try:
        await authorize_review_agent(
            db, principal, agent_id=review.agent_id, org_id=review.org_id
        )
    except AgentReviewServiceError:
        return _receipt("skipped", "requester_unauthorized")

    try:
        version = await _latest_version(db, review)
        if version.model_profile_id is not None and not principal.is_superuser:
            return _receipt("skipped", "profile_forbidden")
        profile = await freeze_review_profile_snapshot(
            db, profile_id=version.model_profile_id
        )
        selected = await select_scheduled_runs(
            db,
            review=review,
            scheduled_for=scheduled_for,
            lookback_days=int(params["lookback_days"]),
        )
    except AgentReviewServiceError as exc:
        return _receipt("skipped", exc.code)
    if not selected:
        return _receipt("skipped", "zero_source_runs")

    try:
        evidence = await build_review_evidence_input(
            db,
            principal,
            review=review,
            version=version,
            requested_run_ids=selected,
        )
    except AgentReviewServiceError as exc:
        return _receipt("skipped", exc.code)

    request_fingerprint = review_request_fingerprint(
        review_id=review.id,
        review_version_id=version.id,
        review_version=version.version,
        selected_run_ids=evidence.selected_run_ids,
        review_input=evidence.input,
        source_refs=evidence.source_refs,
        profile_fingerprint=profile.fingerprint,
    )
    dedupe_material = {
        "schedule_id": str(trigger.id),
        "scheduled_for": scheduled_for.isoformat(),
        "requester": str(principal.user_id),
        "request_fingerprint": request_fingerprint,
    }
    dedupe_key = "agent-review-schedule:" + _hash(dedupe_material)
    # Final readability guard BEFORE any writes: a concurrent revocation
    # between evidence build and enqueue must yield a skipped receipt with
    # no job and no domain row behind it.
    review_run_id = uuid4()
    draft_run = AgentReviewRun(
        id=review_run_id,
        review_id=review.id,
        review_version_id=version.id,
        review_version=version.version,
        agent_id=review.agent_id,
        org_id=review.org_id,
        platform_job_id=None,
        requested_by_user_id=principal.user_id,
        requested_run_ids=list(evidence.selected_run_ids),
        selected_run_ids=evidence.selected_run_ids,
        source_evidence=evidence.input,
        source_refs=evidence.source_refs,
        profile_snapshot=profile_snapshot_to_dict(profile),
        profile_fingerprint=profile.fingerprint,
        request_fingerprint=request_fingerprint,
        input_bytes=evidence.input_bytes,
    )
    try:
        await assert_review_sources_readable(db, principal, review_run=draft_run)
    except AgentReviewServiceError as exc:
        return _receipt("skipped", exc.code)
    job, _reused = await enqueue_platform_job(
        db,
        AGENT_REVIEW_DEFINITION,
        AgentReviewJobPayload(review_run_id=review_run_id),
        dedupe_key=dedupe_key,
        organization_id=review.org_id,
        requested_by_user_id=principal.user_id,
        requested_by_email=principal.email,
        requested_by_name=principal.name or principal.email or "Unknown",
        resource_type="agent_review_run",
        resource_id=str(review_run_id),
        title=f"Scheduled agent review: {review.name}",
        action_url=None,
    )
    run = AgentReviewRun(
        id=review_run_id,
        review_id=review.id,
        review_version_id=version.id,
        review_version=version.version,
        agent_id=review.agent_id,
        org_id=review.org_id,
        platform_job_id=job.id,
        requested_by_user_id=principal.user_id,
        requested_run_ids=list(evidence.selected_run_ids),
        selected_run_ids=evidence.selected_run_ids,
        source_evidence=evidence.input,
        source_refs=evidence.source_refs,
        profile_snapshot=profile_snapshot_to_dict(profile),
        profile_fingerprint=profile.fingerprint,
        request_fingerprint=request_fingerprint,
        input_bytes=evidence.input_bytes,
    )
    db.add(run)
    await db.flush()

    fire.status = "admitted"
    fire.reason = None
    fire.platform_job_id = job.id
    fire.domain_run_id = run.id
    fire.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return fire


async def latest_admitted_job_active(
    db: AsyncSession, *, trigger_id: UUID
) -> bool:
    """True when the latest admitted fire's job is still non-terminal."""
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
    if fire is None or fire.platform_job_id is None:
        return False
    job = await db.get(PlatformJob, fire.platform_job_id)
    if job is None:
        return False
    return job.status in ("queued", "running", "waiting", "cancel_requested")


async def claim_fire(
    db: AsyncSession, *, trigger_id: UUID, scheduled_for: datetime
) -> RecurringTriggerFire:
    """Insert the (trigger, occurrence) fence; existing row when claimed.

    Uses a savepoint so a conflicting insert never discards the caller's
    pending work.
    """
    from sqlalchemy.exc import IntegrityError

    fire = RecurringTriggerFire(
        id=uuid4(),
        trigger_id=trigger_id,
        scheduled_for=scheduled_for,
        status="claimed",
    )
    try:
        async with db.begin_nested():
            db.add(fire)
            await db.flush()
    except IntegrityError:
        existing = (
            await db.execute(
                select(RecurringTriggerFire).where(
                    RecurringTriggerFire.trigger_id == trigger_id,
                    RecurringTriggerFire.scheduled_for == scheduled_for,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    return fire


async def count_fires_for_trigger(db: AsyncSession, *, trigger_id: UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(RecurringTriggerFire)
                .where(RecurringTriggerFire.trigger_id == trigger_id)
            )
        ).scalar_one()
    )
