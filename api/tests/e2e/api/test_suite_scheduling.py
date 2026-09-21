"""Scheduled evaluation-suite admission and matrix parity contracts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.review_schedule_admission import claim_fire
from src.jobs.schedulers.recurring_triggers import (
    process_recurring_platform_job_triggers,
)
from src.models.orm.agent_evaluations import (
    AgentCandidateSnapshot,
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationMatrix,
    AgentEvaluationSuite,
)
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.recurring_triggers import (
    RecurringPlatformJobTrigger,
    RecurringTriggerFire,
)
from shared.suite_schedule_admission import admit_scheduled_suite

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
async def sched_matrix_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Sched Matrix Agent {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
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


@pytest_asyncio.fixture
async def sched_matrix_profile(
    e2e_client, platform_admin, llm_config_cleanup
) -> AsyncGenerator[UUID, None]:
    suffix = uuid4().hex[:8]
    connection = e2e_client.post(
        "/api/admin/ai/connections",
        json={
            "name": f"Sched Matrix Conn A {suffix}",
            "provider": "openai",
            "api_key": "test-only",
            "endpoint": "https://sched-a.example/v1",
        },
        headers=platform_admin.headers,
    )
    assert connection.status_code == 201, connection.text
    connection_a_id = connection.json()["id"]
    profile_a = e2e_client.post(
        "/api/admin/ai/profiles",
        json={
            "name": f"Sched Matrix Profile A {suffix}",
            "connection_id": connection_a_id,
            "model": "model-a-original",
            "enabled_for_chat": True,
        },
        headers=platform_admin.headers,
    )
    assert profile_a.status_code == 201, profile_a.text
    assignment = e2e_client.put(
        "/api/admin/ai/assignments/primary",
        json={"profile_id": profile_a.json()["id"]},
        headers=platform_admin.headers,
    )
    assert assignment.status_code == 200, assignment.text
    connection_b = e2e_client.post(
        "/api/admin/ai/connections",
        json={
            "name": f"Sched Matrix Conn B {suffix}",
            "provider": "openai",
            "api_key": "test-only",
            "endpoint": "https://sched-b.example/v1",
        },
        headers=platform_admin.headers,
    )
    assert connection_b.status_code == 201, connection_b.text
    connection_b_id = connection_b.json()["id"]
    profile_b = e2e_client.post(
        "/api/admin/ai/profiles",
        json={
            "name": f"Sched Matrix Profile B {suffix}",
            "connection_id": connection_b_id,
            "model": "model-b-selected",
            "enabled_for_chat": True,
        },
        headers=platform_admin.headers,
    )
    assert profile_b.status_code == 201, profile_b.text
    profile_b_id = profile_b.json()["id"]
    yield UUID(profile_b_id)
    e2e_client.delete(
        "/api/admin/ai/assignments/primary", headers=platform_admin.headers
    )
    e2e_client.delete(
        f"/api/admin/ai/profiles/{profile_b_id}",
        headers=platform_admin.headers,
    )
    e2e_client.delete(
        f"/api/admin/ai/profiles/{profile_a.json()['id']}",
        headers=platform_admin.headers,
    )
    e2e_client.delete(
        f"/api/admin/ai/connections/{connection_b_id}",
        headers=platform_admin.headers,
    )
    e2e_client.delete(
        f"/api/admin/ai/connections/{connection_a_id}",
        headers=platform_admin.headers,
    )


@pytest_asyncio.fixture
async def sched_published_suite(
    e2e_client, platform_admin, org1, sched_matrix_agent, sched_matrix_profile, db_session: AsyncSession
):
    suite_resp = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={
            "name": f"Sched Matrix Suite {uuid4().hex[:8]}",
            "agent_id": sched_matrix_agent["id"],
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert suite_resp.status_code == 200, suite_resp.text
    suite = suite_resp.json()
    case_resp = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite['id']}/cases",
        json={"name": "sched-matrix-case"},
        headers=platform_admin.headers,
    )
    assert case_resp.status_code == 200, case_resp.text
    pub_resp = e2e_client.post(
        f"/api/agent-evaluations/suites/{suite['id']}/publish",
        headers=platform_admin.headers,
    )
    assert pub_resp.status_code == 200, pub_resp.text
    candidate_resp = e2e_client.post(
        "/api/agent-evaluations/candidates",
        json={
            "base_agent_id": sched_matrix_agent["id"],
            "name": "sched-matrix-candidate",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert candidate_resp.status_code == 200, candidate_resp.text
    yield {"suite": pub_resp.json(), "candidate": candidate_resp.json(), "agent": sched_matrix_agent}
    await db_session.rollback()
    await db_session.execute(
        delete(AgentEvaluationCase).where(
            AgentEvaluationCase.suite_id == UUID(suite["id"])
        )
    )
    await db_session.execute(
        delete(AgentEvaluationSuite).where(
            AgentEvaluationSuite.id == UUID(suite["id"])
        )
    )
    await db_session.execute(
        delete(AgentCandidateSnapshot).where(
            AgentCandidateSnapshot.id == UUID(candidate_resp.json()["id"])
        )
    )
    await db_session.commit()


def _make_suite_trigger(*, org_id: UUID, suite_id: UUID, user, profile_id: UUID, candidate_id: UUID | None = None) -> RecurringPlatformJobTrigger:
    return RecurringPlatformJobTrigger(
        id=uuid4(),
        org_id=org_id,
        operation_type="agent_evaluation_suite",
        operation_id=suite_id,
        operation_params={
            "suite_id": str(suite_id),
            "candidate_ids": [str(candidate_id)] if candidate_id else [],
            "profile_ids": [str(profile_id)],
            "repetitions_override": None,
        },
        cron_expression="* * * * *",
        timezone="UTC",
        enabled=True,
        overlap_policy="skip",
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email,
    )


async def _cancel_and_cleanup_matrix(
    e2e_client,
    platform_admin,
    db_session: AsyncSession,
    matrix_id: UUID,
    trigger_ids: list[UUID],
) -> None:
    """Cancel cells through the shared path, then delete all owned rows."""
    try:
        e2e_client.post(
            f"/api/agent-evaluations/executions/batch/{matrix_id}/cancel",
            headers=platform_admin.headers,
        )
    except Exception as e:
        logger.debug(f"matrix cancel error: {e}")
    await db_session.rollback()
    # Fires first: deleting a job nulls fire.platform_job_id, which would
    # trip the admitted-fire CHECK on rows still referencing it.
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
    matrix = await db_session.get(AgentEvaluationMatrix, matrix_id)
    member_ids = (
        [UUID(value) for value in (matrix.cell_execution_ids or [])]
        if matrix is not None
        else []
    )
    job_ids: list[UUID] = []
    if member_ids:
        executions = (
            (
                await db_session.execute(
                    select(AgentEvaluationExecution).where(
                        AgentEvaluationExecution.id.in_(member_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        job_ids = [e.platform_job_id for e in executions if e.platform_job_id]
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id.in_(member_ids)
            )
        )
    if job_ids:
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
    await db_session.execute(
        delete(AgentEvaluationMatrix).where(AgentEvaluationMatrix.id == matrix_id)
    )
    await db_session.commit()


async def test_scheduled_suite_admits_paired_matrix(
    e2e_client,
    platform_admin,
    org1_user,
    sched_published_suite,
    sched_matrix_profile,
    db_session: AsyncSession,
):
    suite = sched_published_suite["suite"]
    candidate = sched_published_suite["candidate"]
    trigger = _make_suite_trigger(
        org_id=org1_user.organization_id,
        suite_id=UUID(suite["id"]),
        user=org1_user,
        profile_id=sched_matrix_profile,
        candidate_id=UUID(candidate["id"]),
    )
    db_session.add(trigger)
    await db_session.commit()
    tid = trigger.id
    matrix_id: UUID | None = None
    try:
        with patch(PATH_DB_CTX, return_value=_DbCtx(db_session)):
            results = await process_recurring_platform_job_triggers()
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
        assert fire.domain_run_id is not None
        matrix_id = fire.domain_run_id
        # Cancel before reading: no live queued job survives this test.
        cancel_resp = e2e_client.post(
            f"/api/agent-evaluations/executions/batch/{matrix_id}/cancel",
            headers=platform_admin.headers,
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        matrix = await db_session.get(AgentEvaluationMatrix, matrix_id)
        assert matrix is not None
        assert matrix.suite_version == suite["version"]
        assert len(matrix.cell_execution_ids) == 2
        executions = (
            (
                await db_session.execute(
                    select(AgentEvaluationExecution).where(
                        AgentEvaluationExecution.id.in_(
                            [UUID(v) for v in matrix.cell_execution_ids]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(executions) == 2
        for execution in executions:
            assert execution.status == "cancelled"
            assert execution.profile_id == sched_matrix_profile
            assert execution.platform_job_id is not None
    finally:
        if matrix_id is not None:
            await _cancel_and_cleanup_matrix(
                e2e_client, platform_admin, db_session, matrix_id, [tid]
            )
        else:
            await db_session.rollback()
            await db_session.execute(
                delete(RecurringTriggerFire).where(
                    RecurringTriggerFire.trigger_id == tid
                )
            )
            await db_session.execute(
                delete(RecurringPlatformJobTrigger).where(
                    RecurringPlatformJobTrigger.id == tid
                )
            )
            await db_session.commit()


async def test_scheduled_suite_duplicate_and_overlap(
    e2e_client,
    platform_admin,
    org1_user,
    sched_published_suite,
    sched_matrix_profile,
    db_session: AsyncSession,
):
    suite = sched_published_suite["suite"]
    trigger = _make_suite_trigger(
        org_id=org1_user.organization_id,
        suite_id=UUID(suite["id"]),
        user=org1_user,
        profile_id=sched_matrix_profile,
    )
    db_session.add(trigger)
    await db_session.commit()
    tid = trigger.id
    now = datetime.now(timezone.utc)
    old_for = now.replace(second=0, microsecond=0) - timedelta(days=1)
    matrix_ids: list[UUID] = []
    try:
        old = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=old_for
        )
        old = await admit_scheduled_suite(
            db_session, trigger=trigger, fire=old, scheduled_for=old_for
        )
        await db_session.commit()
        assert old.status == "admitted"
        assert old.domain_run_id is not None
        matrix_ids.append(old.domain_run_id)
        # Cancel at once: the duplicate path below needs no live job.
        cancel_resp = e2e_client.post(
            f"/api/agent-evaluations/executions/batch/{old.domain_run_id}/cancel",
            headers=platform_admin.headers,
        )
        assert cancel_resp.status_code == 200, cancel_resp.text

        with patch(PATH_DB_CTX, return_value=_DbCtx(db_session)):
            results = await process_recurring_platform_job_triggers()
        # Cancelled cells do not overlap-block: the due occurrence admits.
        # The old occurrence itself is fenced exactly once either way.
        old_fires = (
            (
                await db_session.execute(
                    select(RecurringTriggerFire).where(
                        RecurringTriggerFire.trigger_id == trigger.id,
                        RecurringTriggerFire.scheduled_for == old_for,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(old_fires) == 1
        assert results["admitted"] >= 1
        new_fire = (
            await db_session.execute(
                select(RecurringTriggerFire).where(
                    RecurringTriggerFire.trigger_id == trigger.id,
                    RecurringTriggerFire.status == "admitted",
                    RecurringTriggerFire.id != old.id,
                )
            )
        ).scalar_one_or_none()
        if new_fire is not None and new_fire.domain_run_id is not None:
            matrix_ids.append(new_fire.domain_run_id)
            cancel_resp = e2e_client.post(
                f"/api/agent-evaluations/executions/batch/{new_fire.domain_run_id}/cancel",
                headers=platform_admin.headers,
            )
            assert cancel_resp.status_code == 200, cancel_resp.text
        matrices = (
            (
                await db_session.execute(
                    select(AgentEvaluationMatrix).where(
                        AgentEvaluationMatrix.suite_id == UUID(suite["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert 1 <= len(matrices) <= 2
    finally:
        for matrix_id in matrix_ids:
            await _cancel_and_cleanup_matrix(
                e2e_client, platform_admin, db_session, matrix_id, [tid]
            )
        await db_session.rollback()
        await db_session.execute(
            delete(RecurringTriggerFire).where(
                RecurringTriggerFire.trigger_id == tid
            )
        )
        await db_session.execute(
            delete(RecurringPlatformJobTrigger).where(
                RecurringPlatformJobTrigger.id == tid
            )
        )
        await db_session.commit()


async def test_scheduled_suite_fail_closed(
    e2e_client,
    platform_admin,
    org1_user,
    sched_published_suite,
    sched_matrix_profile,
    db_session: AsyncSession,
):
    suite = sched_published_suite["suite"]
    # Unpublished draft suite fails closed.
    draft_resp = e2e_client.post(
        "/api/agent-evaluations/suites",
        json={
            "name": f"Sched Draft Suite {uuid4().hex[:8]}",
            "agent_id": sched_published_suite["agent"]["id"],
            "organization_id": str(org1_user.organization_id),
        },
        headers=platform_admin.headers,
    )
    assert draft_resp.status_code == 200, draft_resp.text
    draft_suite_id = UUID(draft_resp.json()["id"])
    now = datetime.now(timezone.utc)
    scheduled_for = now.replace(second=0, microsecond=0)
    triggers = [
        _make_suite_trigger(
            org_id=org1_user.organization_id,
            suite_id=draft_suite_id,
            user=org1_user,
            profile_id=sched_matrix_profile,
        ),
        _make_suite_trigger(
            org_id=org1_user.organization_id,
            suite_id=uuid4(),
            user=org1_user,
            profile_id=sched_matrix_profile,
        ),
    ]
    ghost = _make_suite_trigger(
        org_id=org1_user.organization_id,
        suite_id=UUID(suite["id"]),
        user=org1_user,
        profile_id=sched_matrix_profile,
    )
    ghost.requested_by_user_id = uuid4()
    triggers.append(ghost)
    db_session.add_all(triggers)
    await db_session.commit()
    trigger_ids = [t.id for t in triggers]
    try:
        expected = {
            trigger_ids[0]: "suite_not_published",
            trigger_ids[1]: "missing_target",
            trigger_ids[2]: "requester_unauthorized",
        }
        for trigger in triggers:
            fire = await claim_fire(
                db_session, trigger_id=trigger.id, scheduled_for=scheduled_for
            )
            fire = await admit_scheduled_suite(
                db_session, trigger=trigger, fire=fire, scheduled_for=scheduled_for
            )
            assert fire.status == "skipped", trigger.id
            assert fire.reason == expected[trigger.id], fire.reason
            assert fire.platform_job_id is None
        await db_session.commit()
        matrices = (
            (
                await db_session.execute(
                    select(AgentEvaluationMatrix).where(
                        AgentEvaluationMatrix.suite_id.in_(
                            [draft_suite_id, UUID(suite["id"])]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert matrices == []
    finally:
        await db_session.rollback()
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
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id == draft_suite_id
            )
        )
        await db_session.commit()


async def test_scheduled_suite_overlap_skips_while_cells_active(
    e2e_client,
    platform_admin,
    org1_user,
    sched_published_suite,
    sched_matrix_profile,
    db_session: AsyncSession,
):
    """Overlap needs a live job across a processor run.

    This keeps one queued job alive for seconds — the same exposure every
    on-demand admission test already accepts (worker logs show zero such
    claims; cleanup cancels-then-deletes). No narrower deterministic
    construction exists: overlap is defined over live cell state.
    """
    suite = sched_published_suite["suite"]
    trigger = _make_suite_trigger(
        org_id=org1_user.organization_id,
        suite_id=UUID(suite["id"]),
        user=org1_user,
        profile_id=sched_matrix_profile,
    )
    db_session.add(trigger)
    await db_session.commit()
    tid = trigger.id
    now = datetime.now(timezone.utc)
    old_for = now.replace(second=0, microsecond=0) - timedelta(days=1)
    matrix_id: UUID | None = None
    try:
        old = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=old_for
        )
        old = await admit_scheduled_suite(
            db_session, trigger=trigger, fire=old, scheduled_for=old_for
        )
        await db_session.commit()
        assert old.status == "admitted"
        matrix_id = old.domain_run_id

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
        matrices = (
            (
                await db_session.execute(
                    select(AgentEvaluationMatrix).where(
                        AgentEvaluationMatrix.suite_id == UUID(suite["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(matrices) == 1
    finally:
        if matrix_id is not None:
            await _cancel_and_cleanup_matrix(
                e2e_client, platform_admin, db_session, matrix_id, [tid]
            )


async def test_mid_batch_failure_leaves_no_partial_rows(
    e2e_client,
    platform_admin,
    org1_user,
    sched_published_suite,
    sched_matrix_profile,
    db_session: AsyncSession,
):
    """A second-cell failure rolls back the first cell's flushed rows."""
    suite = sched_published_suite["suite"]
    candidate = sched_published_suite["candidate"]
    trigger = _make_suite_trigger(
        org_id=org1_user.organization_id,
        suite_id=UUID(suite["id"]),
        user=org1_user,
        profile_id=sched_matrix_profile,
        candidate_id=UUID(candidate["id"]),
    )
    # Poison the candidate cell: baseline cell admits first, then the
    # candidate lookup fails after its rows are flushed.
    trigger.operation_params = {
        "suite_id": suite["id"],
        "candidate_ids": [candidate["id"], str(uuid4())],
        "profile_ids": [str(sched_matrix_profile)],
        "repetitions_override": None,
    }
    db_session.add(trigger)
    await db_session.commit()
    tid = trigger.id
    now = datetime.now(timezone.utc)
    scheduled_for = now.replace(second=0, microsecond=0)
    try:
        fire = await claim_fire(
            db_session, trigger_id=trigger.id, scheduled_for=scheduled_for
        )
        fire = await admit_scheduled_suite(
            db_session, trigger=trigger, fire=fire, scheduled_for=scheduled_for
        )
        await db_session.commit()
        assert fire.status == "skipped"
        assert fire.reason == "candidate_not_found"
        assert fire.platform_job_id is None
        assert fire.domain_run_id is None
        matrices = (
            (
                await db_session.execute(
                    select(AgentEvaluationMatrix).where(
                        AgentEvaluationMatrix.suite_id == UUID(suite["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert matrices == []
        executions = (
            (
                await db_session.execute(
                    select(AgentEvaluationExecution).where(
                        AgentEvaluationExecution.suite_id == UUID(suite["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert executions == []
        # The baseline cell's enqueued job must roll back with the batch:
        # resource_id carries no FK, so only an explicit check proves it.
        jobs = (
            (
                await db_session.execute(
                    select(PlatformJob).where(
                        PlatformJob.resource_type == "agent_evaluation",
                        PlatformJob.title == f"Evaluating suite {suite['name']}",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert jobs == []
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(RecurringTriggerFire).where(
                RecurringTriggerFire.trigger_id == tid
            )
        )
        await db_session.execute(
            delete(RecurringPlatformJobTrigger).where(
                RecurringPlatformJobTrigger.id == tid
            )
        )
        await db_session.commit()
