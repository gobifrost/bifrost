"""Scheduled review admission and trigger processor contracts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.agent_reviews import AgentReviewServiceError
from shared.review_schedule_admission import (
    admit_scheduled_review,
    claim_fire,
    select_scheduled_runs,
)
from src.jobs.schedulers.recurring_triggers import (
    process_recurring_platform_job_triggers,
)
from src.models.orm.agent_reviews import (
    AgentReviewDefinition,
    AgentReviewRun,
    AgentReviewVersion,
)
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.recurring_triggers import (
    RecurringPlatformJobTrigger,
    RecurringTriggerFire,
)
from src.models.orm.users import User
from src.services.agent_runtime import types as runtime_types
from src.services.ai_model_service import AIModelService

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio

PATH_DB_CTX = "src.jobs.schedulers.recurring_triggers.get_db_context"


class _DbCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return False


@pytest_asyncio.fixture
async def review_profile(db_session: AsyncSession):
    prior = await db_session.get(AIModelAssignment, "testing")
    prior_profile_id = prior.profile_id if prior is not None else None
    connection = AIProviderConnection(
        id=uuid4(),
        name=f"sched-review-provider-{uuid4().hex[:8]}",
        provider="openai",
        encrypted_api_key=AIModelService(db_session).encrypt_api_key("test-key"),
    )
    db_session.add(connection)
    await db_session.flush()
    profile = AIModelProfile(
        id=uuid4(),
        name=f"sched-review-profile-{uuid4().hex[:8]}",
        connection_id=connection.id,
        model="gpt-review-sched-test",
        default_max_tokens=123,
    )
    db_session.add(profile)
    await db_session.flush()
    if prior is None:
        db_session.add(AIModelAssignment(assignment_key="testing", profile_id=profile.id))
    else:
        prior.profile_id = profile.id
    await db_session.commit()
    profile_id = profile.id
    connection_id = connection.id
    yield profile
    await db_session.rollback()
    assignment = await db_session.get(AIModelAssignment, "testing")
    if prior_profile_id is None:
        if assignment is not None:
            await db_session.delete(assignment)
    elif assignment is not None:
        assignment.profile_id = prior_profile_id
    else:
        db_session.add(AIModelAssignment(assignment_key="testing", profile_id=prior_profile_id))
    await db_session.flush()
    await db_session.execute(delete(AIModelProfile).where(AIModelProfile.id == profile_id))
    await db_session.execute(delete(AIProviderConnection).where(AIProviderConnection.id == connection_id))
    await db_session.commit()


@pytest_asyncio.fixture
async def sched_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Sched Review Agent {uuid4().hex[:8]}",
            "system_prompt": "Answer helpfully.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    yield agent
    try:
        e2e_client.delete(f"/api/agents/{agent['id']}", headers=platform_admin.headers)
    except Exception as e:
        logger.debug(f"fixture cleanup error: {e}")


def _requester_snapshot(user) -> dict:
    return {
        "requested_by_user_id": user.user_id,
        "requested_by_email": user.email,
        "requested_by_name": user.name or user.email,
    }


async def _make_review(e2e_client, platform_admin, agent_id: str) -> dict:
    resp = e2e_client.post(
        "/api/agent-reviews",
        json={
            "agent_id": agent_id,
            "name": f"Sched Review {uuid4().hex[:8]}",
            "review_statement": "Find problems in these runs.",
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _seed_run(
    db_session: AsyncSession,
    *,
    agent_id: UUID,
    org_id: UUID,
    user_id: UUID,
    completed_at: datetime,
    trigger_type: str = "api",
    status: str = "completed",
) -> AgentRun:
    run = AgentRun(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org_id,
        trigger_type=trigger_type,
        status=status,
        caller_user_id=str(user_id),
        input={"message": "hi"},
        output={"text": "ok"},
        completed_at=completed_at,
    )
    run.root_run_id = run.id
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        AgentRunJournalEntry(
            run_id=run.id,
            sequence=1,
            kind=runtime_types.JOURNAL_COMPLETION,
            data={"status": "completed"},
        )
    )
    await db_session.commit()
    return run


def _make_trigger(
    *,
    org_id: UUID,
    review_id: UUID,
    user,
    lookback_days: int = 7,
    enabled: bool = True,
) -> RecurringPlatformJobTrigger:
    snap = _requester_snapshot(user)
    return RecurringPlatformJobTrigger(
        id=uuid4(),
        org_id=org_id,
        operation_type="agent_review",
        operation_id=review_id,
        operation_params={"review_id": str(review_id), "lookback_days": lookback_days},
        cron_expression="* * * * *",
        timezone="UTC",
        enabled=enabled,
        overlap_policy="skip",
        requested_by_user_id=snap["requested_by_user_id"],
        requested_by_email=snap["requested_by_email"],
        requested_by_name=snap["requested_by_name"],
    )


async def _cleanup_scheduling(
    db_session: AsyncSession,
    *,
    trigger_ids: list[UUID],
    review_ids: list[UUID],
    run_ids: list[UUID],
) -> None:
    await db_session.rollback()
    if trigger_ids:
        await db_session.execute(
            delete(RecurringTriggerFire).where(
                RecurringTriggerFire.trigger_id.in_(trigger_ids)
            )
        )
        await db_session.execute(
            delete(RecurringPlatformJobTrigger).where(
                RecurringPlatformJobTrigger.id.in_(trigger_ids)
            )
        )
    if review_ids:
        runs = (
            (
                await db_session.execute(
                    select(AgentReviewRun).where(
                        AgentReviewRun.review_id.in_(review_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        job_ids = [run.platform_job_id for run in runs if run.platform_job_id]
        run_row_ids = [run.id for run in runs]
        if run_row_ids:
            await db_session.execute(
                delete(AIUsage).where(
                    AIUsage.quality_operation_type == "agent_review",
                    AIUsage.quality_operation_id.in_(run_row_ids),
                )
            )
            await db_session.execute(
                delete(AIUsageAttempt).where(
                    AIUsageAttempt.quality_operation_type == "agent_review",
                    AIUsageAttempt.quality_operation_id.in_(run_row_ids),
                )
            )
            await db_session.execute(
                delete(AgentFinding).where(
                    AgentFinding.source_review_run_id.in_(run_row_ids)
                )
            )
            await db_session.execute(
                delete(AgentReviewRun).where(AgentReviewRun.id.in_(run_row_ids))
            )
        await db_session.execute(
            delete(AgentReviewVersion).where(
                AgentReviewVersion.review_id.in_(review_ids)
            )
        )
        await db_session.execute(
            delete(AgentReviewDefinition).where(
                AgentReviewDefinition.id.in_(review_ids)
            )
        )
        if job_ids:
            # Cancel first so a claimed job can never execute after cleanup.
            for job_id in job_ids:
                job = await db_session.get(PlatformJob, job_id)
                if job is not None and job.status in (
                    "queued",
                    "running",
                    "waiting",
                    "cancel_requested",
                ):
                    job.status = "cancelled"
            await db_session.flush()
            await db_session.execute(
                delete(PlatformJob).where(PlatformJob.id.in_(job_ids))
            )
    if run_ids:
        await db_session.execute(
            delete(AgentRunJournalEntry).where(
                AgentRunJournalEntry.run_id.in_(run_ids)
            )
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
    await db_session.commit()


async def test_scheduled_review_admits_job_and_fire(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    run_old = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=now - timedelta(hours=2),
    )
    run_new = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=now - timedelta(hours=1),
    )
    trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
    )
    db_session.add(trigger)
    await db_session.commit()
    scheduled_for = now.replace(second=0, microsecond=0)
    try:
        selected = await select_scheduled_runs(
            db_session,
            review=await db_session.get(
                AgentReviewDefinition, UUID(review["id"])
            ),
            scheduled_for=scheduled_for,
            lookback_days=7,
        )
        assert selected == [run_new.id, run_old.id]

        fire = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=scheduled_for
        )
        fire = await admit_scheduled_review(
            db_session, trigger=trigger, fire=fire, scheduled_for=scheduled_for
        )
        await db_session.commit()
        assert fire.status == "admitted"
        assert fire.platform_job_id is not None
        assert fire.domain_run_id is not None

        run_row = await db_session.get(AgentReviewRun, fire.domain_run_id)
        assert run_row is not None
        # Evidence admission re-sorts deterministically; selection order
        # (newest first) governs the cap, storage order is canonical.
        assert run_row.selected_run_ids == sorted(
            [run_new.id, run_old.id], key=str
        )
        assert set(run_row.selected_run_ids) == {run_new.id, run_old.id}
        assert run_row.requested_by_user_id == org1_user.user_id
        job = await db_session.get(PlatformJob, fire.platform_job_id)
        assert job is not None
        assert job.status == "queued"
        assert job.requested_by_user_id == str(org1_user.user_id)
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[trigger.id],
            review_ids=[UUID(review["id"])],
            run_ids=[run_old.id, run_new.id],
        )


async def test_duplicate_fire_not_readmitted(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    run = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=now - timedelta(hours=1),
    )
    trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
    )
    db_session.add(trigger)
    await db_session.commit()
    scheduled_for = now.replace(second=0, microsecond=0)
    try:
        first = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=scheduled_for
        )
        await db_session.commit()
        second = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=scheduled_for
        )
        await db_session.commit()
        assert second.id == first.id

        admitted = await admit_scheduled_review(
            db_session, trigger=trigger, fire=first, scheduled_for=scheduled_for
        )
        await db_session.commit()
        assert admitted.status == "admitted"

        with patch(PATH_DB_CTX, return_value=_DbCtx(db_session)):
            results = await process_recurring_platform_job_triggers()
        # Same minute: fence duplicate. Rolled-over minute: overlap skip
        # against the still-queued job. Either way, no second admission.
        assert results["admitted"] == 0
        assert results["duplicates"] >= 1 or results["skipped"] >= 1
        jobs = (
            (
                await db_session.execute(
                    select(PlatformJob).where(
                        PlatformJob.resource_type == "agent_review_run",
                        PlatformJob.resource_id == str(admitted.domain_run_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[trigger.id],
            review_ids=[UUID(review["id"])],
            run_ids=[run.id],
        )


async def test_overlap_skip_while_job_active(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    old_for = now.replace(second=0, microsecond=0) - timedelta(days=1)
    run = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=old_for - timedelta(hours=1),
    )
    trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
    )
    db_session.add(trigger)
    await db_session.commit()
    try:
        # Admit an older occurrence directly so its job stays active.
        old = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=old_for
        )
        old = await admit_scheduled_review(
            db_session, trigger=trigger, fire=old, scheduled_for=old_for
        )
        await db_session.commit()
        assert old.status == "admitted"

        # The processor's due occurrence is unclaimed but must skip overlap.
        with patch(PATH_DB_CTX, return_value=_DbCtx(db_session)):
            results = await process_recurring_platform_job_triggers()
        assert results["admitted"] == 0
        assert results["skipped"] >= 1
        skipped = (
            (
                await db_session.execute(
                    select(RecurringTriggerFire).where(
                        RecurringTriggerFire.trigger_id == trigger.id,
                        RecurringTriggerFire.status == "skipped",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any(row.reason == "overlap" for row in skipped)

        review_runs = (
            (
                await db_session.execute(
                    select(AgentReviewRun).where(
                        AgentReviewRun.review_id == UUID(review["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(review_runs) == 1
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[trigger.id],
            review_ids=[UUID(review["id"])],
            run_ids=[run.id],
        )


async def test_zero_runs_skipped_without_job(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
        lookback_days=1,
    )
    db_session.add(trigger)
    await db_session.commit()
    scheduled_for = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    try:
        fire = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=scheduled_for
        )
        fire = await admit_scheduled_review(
            db_session, trigger=trigger, fire=fire, scheduled_for=scheduled_for
        )
        await db_session.commit()
        assert fire.status == "skipped"
        assert fire.reason == "zero_source_runs"
        assert fire.platform_job_id is None
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[trigger.id],
            review_ids=[UUID(review["id"])],
            run_ids=[],
        )


async def test_lost_requester_and_missing_target_fail_closed(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    run = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=now - timedelta(hours=1),
    )
    ghost = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
    )
    ghost.requested_by_user_id = uuid4()
    orphan = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=uuid4(),
        user=org1_user,
    )
    db_session.add_all([ghost, orphan])
    await db_session.commit()
    scheduled_for = now.replace(second=0, microsecond=0)
    try:
        fire_ghost = await claim_fire(
            db_session, trigger_id=ghost.id, scheduled_for=scheduled_for
        )
        fire_ghost = await admit_scheduled_review(
            db_session, trigger=ghost, fire=fire_ghost, scheduled_for=scheduled_for
        )
        assert fire_ghost.status == "skipped"
        assert fire_ghost.reason == "requester_unauthorized"
        assert fire_ghost.platform_job_id is None

        fire_orphan = await claim_fire(
            db_session, trigger_id=orphan.id, scheduled_for=scheduled_for
        )
        fire_orphan = await admit_scheduled_review(
            db_session, trigger=orphan, fire=fire_orphan, scheduled_for=scheduled_for
        )
        assert fire_orphan.status == "skipped"
        assert fire_orphan.reason == "missing_target"
        assert fire_orphan.platform_job_id is None
        await db_session.commit()
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[ghost.id, orphan.id],
            review_ids=[UUID(review["id"])],
            run_ids=[run.id],
        )


async def test_disabled_review_and_trigger_skip(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    run = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=now - timedelta(hours=1),
    )
    disabled = e2e_client.patch(
        f"/api/agent-reviews/{review['id']}",
        json={"status": "disabled"},
        headers=platform_admin.headers,
    )
    assert disabled.status_code == 200, disabled.text
    trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
    )
    idle = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
        enabled=False,
    )
    db_session.add_all([trigger, idle])
    await db_session.commit()
    scheduled_for = now.replace(second=0, microsecond=0)
    try:
        fire = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=scheduled_for
        )
        fire = await admit_scheduled_review(
            db_session, trigger=trigger, fire=fire, scheduled_for=scheduled_for
        )
        assert fire.status == "skipped"
        assert fire.reason == "review_disabled"

        with patch(PATH_DB_CTX, return_value=_DbCtx(db_session)):
            results = await process_recurring_platform_job_triggers()
        # Disabled trigger is never due-evaluated; the disabled review's
        # trigger claims its fence but skips without a job.
        assert results["admitted"] == 0
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[trigger.id, idle.id],
            review_ids=[UUID(review["id"])],
            run_ids=[run.id],
        )


async def test_processor_admits_due_trigger_end_to_end(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    run = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=now - timedelta(minutes=30),
    )
    trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
    )
    db_session.add(trigger)
    await db_session.commit()
    try:
        with patch(PATH_DB_CTX, return_value=_DbCtx(db_session)):
            results = await process_recurring_platform_job_triggers()
        assert results["due"] >= 1
        assert results["admitted"] >= 1
        fire = (
            await db_session.execute(
                select(RecurringTriggerFire).where(
                    RecurringTriggerFire.trigger_id == trigger.id
                )
            )
        ).scalar_one()
        assert fire.status == "admitted"
        assert fire.platform_job_id is not None
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[trigger.id],
            review_ids=[UUID(review["id"])],
            run_ids=[run.id],
        )


async def test_selection_includes_all_terminal_statuses(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    run_ids: list[UUID] = []
    try:
        for i, status in enumerate(
            ["completed", "failed", "cancelled", "timeout", "budget_exceeded", "contract_failed"]
        ):
            run = await _seed_run(
                db_session,
                agent_id=UUID(sched_agent["id"]),
                org_id=org1_user.organization_id,
                user_id=org1_user.user_id,
                completed_at=now - timedelta(hours=i + 1),
                status=status,
            )
            run_ids.append(run.id)
        queued = await _seed_run(
            db_session,
            agent_id=UUID(sched_agent["id"]),
            org_id=org1_user.organization_id,
            user_id=org1_user.user_id,
            completed_at=now - timedelta(minutes=10),
            status="queued",
        )
        run_ids.append(queued.id)
        synthetic = await _seed_run(
            db_session,
            agent_id=UUID(sched_agent["id"]),
            org_id=org1_user.organization_id,
            user_id=org1_user.user_id,
            completed_at=now - timedelta(minutes=5),
            trigger_type="evaluation_synthetic",
        )
        run_ids.append(synthetic.id)
        selected = await select_scheduled_runs(
            db_session,
            review=await db_session.get(
                AgentReviewDefinition, UUID(review["id"])
            ),
            scheduled_for=now,
            lookback_days=7,
        )
        assert len(selected) == 6
        assert queued.id not in selected
        assert synthetic.id not in selected
        # Newest terminal first.
        assert selected[0] == run_ids[0]
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[],
            review_ids=[UUID(review["id"])],
            run_ids=run_ids,
        )


async def test_superuser_requester_admitted_system_requester_rejected(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    run = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=now - timedelta(hours=1),
    )
    sys_user = User(
        id=uuid4(),
        email=f"sched-system-{uuid4().hex[:8]}@example.invalid",
        is_active=True,
        is_superuser=True,
        is_system=True,
        organization_id=None,
    )
    db_session.add(sys_user)
    await db_session.flush()
    admin_trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=platform_admin,
    )
    sys_trigger = RecurringPlatformJobTrigger(
        id=uuid4(),
        org_id=org1_user.organization_id,
        operation_type="agent_review",
        operation_id=UUID(review["id"]),
        operation_params={"review_id": review["id"], "lookback_days": 7},
        cron_expression="* * * * *",
        timezone="UTC",
        enabled=True,
        overlap_policy="skip",
        requested_by_user_id=sys_user.id,
        requested_by_email=sys_user.email,
        requested_by_name="Scheduler System",
    )
    db_session.add_all([admin_trigger, sys_trigger])
    await db_session.commit()
    scheduled_for = now.replace(second=0, microsecond=0)
    try:
        admin_fire = await claim_fire(
            db_session, trigger_id=admin_trigger.id, scheduled_for=scheduled_for
        )
        admin_fire = await admit_scheduled_review(
            db_session,
            trigger=admin_trigger,
            fire=admin_fire,
            scheduled_for=scheduled_for,
        )
        # Platform admin requester follows HTTP authorization semantics.
        assert admin_fire.status == "admitted"
        # Cancel the admin job at once so no executable job lingers.
        if admin_fire.platform_job_id is not None:
            job = await db_session.get(PlatformJob, admin_fire.platform_job_id)
            if job is not None:
                job.status = "cancelled"
            await db_session.flush()

        sys_fire = await claim_fire(
            db_session, trigger_id=sys_trigger.id, scheduled_for=scheduled_for
        )
        sys_fire = await admit_scheduled_review(
            db_session, trigger=sys_trigger, fire=sys_fire, scheduled_for=scheduled_for
        )
        assert sys_fire.status == "skipped"
        assert sys_fire.reason == "requester_unauthorized"
        assert sys_fire.platform_job_id is None
        await db_session.commit()
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[admin_trigger.id, sys_trigger.id],
            review_ids=[UUID(review["id"])],
            run_ids=[run.id],
        )
        await db_session.execute(delete(User).where(User.id == sys_user.id))
        await db_session.commit()


async def test_overlap_policy_check_rejects_non_skip(
    e2e_client,
    platform_admin,
    org1_user,
    sched_agent,
    db_session: AsyncSession,
):
    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
    )
    trigger.overlap_policy = "queue"
    db_session.add(trigger)
    try:
        with pytest.raises(IntegrityError):
            await db_session.flush()
    finally:
        await db_session.rollback()
        e2e_client.delete(
            f"/api/agent-reviews/{review['id']}", headers=platform_admin.headers
        )


async def test_failed_guard_leaves_no_orphan_rows(
    e2e_client,
    platform_admin,
    org1_user,
    review_profile,
    sched_agent,
    db_session: AsyncSession,
):
    """A late readability failure must not leave a job or run behind."""
    from unittest.mock import AsyncMock

    review = await _make_review(e2e_client, platform_admin, sched_agent["id"])
    now = datetime.now(timezone.utc)
    run = await _seed_run(
        db_session,
        agent_id=UUID(sched_agent["id"]),
        org_id=org1_user.organization_id,
        user_id=org1_user.user_id,
        completed_at=now - timedelta(hours=1),
    )
    trigger = _make_trigger(
        org_id=org1_user.organization_id,
        review_id=UUID(review["id"]),
        user=org1_user,
    )
    db_session.add(trigger)
    await db_session.commit()
    scheduled_for = now.replace(second=0, microsecond=0)
    try:
        fire = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=scheduled_for
        )
        with patch(
            "shared.review_schedule_admission.assert_review_sources_readable",
            new=AsyncMock(
                side_effect=AgentReviewServiceError("source_run_not_found")
            ),
        ):
            fire = await admit_scheduled_review(
                db_session, trigger=trigger, fire=fire, scheduled_for=scheduled_for
            )
        await db_session.commit()
        assert fire.status == "skipped"
        assert fire.platform_job_id is None
        assert fire.domain_run_id is None
        runs = (
            (
                await db_session.execute(
                    select(AgentReviewRun).where(
                        AgentReviewRun.review_id == UUID(review["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert runs == []
        jobs = (
            (
                await db_session.execute(
                    select(PlatformJob).where(
                        PlatformJob.resource_type == "agent_review_run",
                        PlatformJob.title.like("Scheduled agent review:%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert jobs == []
    finally:
        await _cleanup_scheduling(
            db_session,
            trigger_ids=[trigger.id],
            review_ids=[UUID(review["id"])],
            run_ids=[run.id],
        )
