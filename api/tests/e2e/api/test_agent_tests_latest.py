"""Agent-wide latest results (simulation + recorded, Phase 4c)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_evaluations import (
    AgentEvaluationCase,
    AgentEvaluationExecution,
    AgentEvaluationResult,
    AgentEvaluationSuite,
)
from src.models.orm.agent_recorded_evaluations import (
    AgentRecordedEvaluation,
    AgentRecordedEvaluationResult,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.platform_jobs import PlatformJob

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def latest_agent(e2e_client, platform_admin, org1) -> AsyncGenerator[dict, None]:
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Latest Agent {uuid4().hex[:8]}",
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


async def _cleanup_latest(
    db_session: AsyncSession,
    *,
    agent_id: UUID,
    job_ids: list[UUID],
    run_ids: list[UUID],
) -> None:
    await db_session.rollback()
    eval_ids = (
        (
            await db_session.execute(
                select(AgentRecordedEvaluation.id).where(
                    AgentRecordedEvaluation.agent_id == agent_id
                )
            )
        )
        .scalars()
        .all()
    )
    if eval_ids:
        await db_session.execute(
            delete(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id.in_(eval_ids)
            )
        )
        await db_session.execute(
            delete(AgentRecordedEvaluation).where(
                AgentRecordedEvaluation.id.in_(eval_ids)
            )
        )
    exec_ids = (
        (
            await db_session.execute(
                select(AgentEvaluationExecution.id).where(
                    AgentEvaluationExecution.baseline_agent_id == agent_id
                )
            )
        )
        .scalars()
        .all()
    )
    if exec_ids:
        await db_session.execute(
            delete(AgentEvaluationResult).where(
                AgentEvaluationResult.execution_id.in_(exec_ids)
            )
        )
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id.in_(exec_ids)
            )
        )
    if job_ids:
        await db_session.execute(
            delete(PlatformJob).where(PlatformJob.id.in_(job_ids))
        )
    if run_ids:
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
    suite_ids = (
        (
            await db_session.execute(
                select(AgentEvaluationSuite.id).where(
                    AgentEvaluationSuite.agent_id == agent_id
                )
            )
        )
        .scalars()
        .all()
    )
    if suite_ids:
        await db_session.execute(
            delete(AgentEvaluationCase).where(
                AgentEvaluationCase.suite_id.in_(suite_ids)
            )
        )
        await db_session.execute(
            delete(AgentEvaluationSuite).where(
                AgentEvaluationSuite.id.in_(suite_ids)
            )
        )
    await db_session.commit()


async def _seed_simulation(
    db_session: AsyncSession,
    *,
    agent_id: UUID,
    org_id: UUID,
    case_id: UUID,
    case_version: int,
    status: str,
    profile_id: UUID | None = None,
    candidate_id: UUID | None = None,
    execution_status: str = "succeeded",
    minutes_ago: int = 10,
) -> UUID:
    execution = AgentEvaluationExecution(
        id=uuid4(),
        suite_id=(
            await db_session.execute(
                select(AgentEvaluationCase.suite_id).where(
                    AgentEvaluationCase.id == case_id
                )
            )
        ).scalar_one(),
        suite_version=1,
        candidate_id=candidate_id,
        profile_id=profile_id,
        baseline_agent_id=agent_id,
        status=execution_status,
        total_cases=1,
        completed_cases=1,
    )
    db_session.add(execution)
    await db_session.flush()
    result = AgentEvaluationResult(
        id=uuid4(),
        execution_id=execution.id,
        case_id=case_id,
        case_version=case_version,
        status=status,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    # Force deterministic ordering regardless of insert order.
    db_session.add(result)
    await db_session.flush()
    return execution.id


async def _seed_recorded(
    db_session: AsyncSession,
    *,
    agent_id: UUID,
    org_id: UUID,
    user,
    case_id: UUID,
    case_version: int,
    outcome: str,
    complete: bool,
    minutes_ago: int = 5,
) -> tuple[UUID, UUID]:
    job = PlatformJob(
        id=uuid4(),
        job_type="recorded_test_seed",
        payload={},
        status="succeeded",
        organization_id=org_id,
        requested_by_user_id=str(user.user_id),
        requested_by_email=user.email,
        requested_by_name=user.name or user.email,
        resource_type="agent_recorded_evaluation",
        resource_id=str(uuid4()),
        title="Seed recorded evaluation",
    )
    db_session.add(job)
    await db_session.flush()
    run = AgentRun(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org_id,
        trigger_type="manual",
        status="completed",
        caller_user_id=str(user.user_id),
        input={"message": "hi"},
        output={"text": "ok"},
        completed_at=datetime.now(timezone.utc),
    )
    run.root_run_id = run.id
    db_session.add(run)
    await db_session.flush()
    evaluation = AgentRecordedEvaluation(
        id=uuid4(),
        org_id=org_id,
        agent_id=agent_id,
        requested_by_user_id=str(user.user_id),
        requested_by_email=user.email,
        platform_job_id=job.id,
        frozen_input={
            "judge_mode": "exact",
            "runs": [
                {
                    "source_runs": [
                        {
                            "run_id": str(run.id),
                            "agent_id": str(agent_id),
                        }
                    ]
                }
            ],
        },
    )
    db_session.add(evaluation)
    await db_session.flush()
    db_session.add(
        AgentRecordedEvaluationResult(
            id=uuid4(),
            evaluation_id=evaluation.id,
            case_id=case_id,
            case_version=case_version,
            run_id=run.id,
            applicability="applicable",
            applicability_source="test",
            outcome=outcome,
            complete=complete,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        )
    )
    await db_session.commit()
    return job.id, run.id


async def test_latest_unknown_until_evidence(
    e2e_client, org1_user, org2_user, latest_agent, db_session: AsyncSession
):
    agent_id = latest_agent["id"]
    try:
        created = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json={"name": "Untested test"},
            headers=org1_user.headers,
        )
        assert created.status_code == 201, created.text
        logical_id = created.json()["logical_test_id"]

        page = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/latest",
            headers=org1_user.headers,
        )
        assert page.status_code == 200, page.text
        items = page.json()["items"]
        assert len(items) == 1
        assert items[0]["logical_test_id"] == logical_id
        assert items[0]["simulation"] is None
        assert items[0]["recorded"] is None

        assert (
            e2e_client.get(
                f"/api/agent-evaluations/agents/{agent_id}/tests/latest",
                headers=org2_user.headers,
            ).status_code
            == 404
        )
    finally:
        await _cleanup_latest(
            db_session, agent_id=UUID(agent_id), job_ids=[], run_ids=[]
        )


async def test_latest_prefers_current_version_and_terminal(
    e2e_client, org1_user, latest_agent, db_session: AsyncSession
):
    agent_id = latest_agent["id"]
    agent_uuid = UUID(agent_id)
    org_id = org1_user.organization_id
    job_ids: list[UUID] = []
    try:
        created = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json={"name": "Versioned test"},
            headers=org1_user.headers,
        )
        assert created.status_code == 201, created.text
        logical_id = created.json()["logical_test_id"]
        v1_case = UUID(created.json()["case_id"])
        edited = e2e_client.patch(
            f"/api/agent-evaluations/agents/{agent_id}/tests/{logical_id}",
            json={"name": "Versioned test v2"},
            headers=org1_user.headers,
        )
        assert edited.status_code == 200, edited.text
        v2_case = UUID(edited.json()["case_id"])

        # Old-version and pending results never describe the current test.
        await _seed_simulation(
            db_session,
            agent_id=agent_uuid,
            org_id=org_id,
            case_id=v1_case,
            case_version=1,
            status="passed",
            minutes_ago=30,
        )
        await _seed_simulation(
            db_session,
            agent_id=agent_uuid,
            org_id=org_id,
            case_id=v2_case,
            case_version=2,
            status="pending",
            minutes_ago=20,
        )
        # A running row on a cancelled execution is in-progress evidence.
        await _seed_simulation(
            db_session,
            agent_id=agent_uuid,
            org_id=org_id,
            case_id=v2_case,
            case_version=2,
            status="running",
            execution_status="cancelled",
            minutes_ago=15,
        )
        await db_session.commit()
        page = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/latest",
            headers=org1_user.headers,
        )
        assert page.json()["items"][0]["simulation"] is None

        # A terminal current-version result is reported with context.
        exec_id = await _seed_simulation(
            db_session,
            agent_id=agent_uuid,
            org_id=org_id,
            case_id=v2_case,
            case_version=2,
            status="failed",
            minutes_ago=5,
        )
        await db_session.commit()
        page = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/latest",
            headers=org1_user.headers,
        )
        sim = page.json()["items"][0]["simulation"]
        assert sim["status"] == "failed"
        assert sim["case_version"] == 2
        assert sim["execution_id"] == str(exec_id)
        assert sim["profile_id"] is None
        assert sim["candidate_id"] is None
    finally:
        await _cleanup_latest(
            db_session, agent_id=agent_uuid, job_ids=job_ids, run_ids=[]
        )


async def test_latest_recorded_complete_only(
    e2e_client, org1_user, latest_agent, db_session: AsyncSession
):
    agent_id = latest_agent["id"]
    agent_uuid = UUID(agent_id)
    org_id = org1_user.organization_id
    job_ids: list[UUID] = []
    run_ids: list[UUID] = []
    try:
        created = e2e_client.post(
            f"/api/agent-evaluations/agents/{agent_id}/tests",
            json={"name": "Recorded test"},
            headers=org1_user.headers,
        )
        assert created.status_code == 201, created.text
        case_id = UUID(created.json()["case_id"])

        job_id, run_id = await _seed_recorded(
            db_session,
            agent_id=agent_uuid,
            org_id=org_id,
            user=org1_user,
            case_id=case_id,
            case_version=1,
            outcome="passed",
            complete=False,
            minutes_ago=30,
        )
        job_ids.append(job_id)
        run_ids.append(run_id)
        page = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/latest",
            headers=org1_user.headers,
        )
        assert page.json()["items"][0]["recorded"] is None

        job_id, run_id = await _seed_recorded(
            db_session,
            agent_id=agent_uuid,
            org_id=org_id,
            user=org1_user,
            case_id=case_id,
            case_version=1,
            outcome="failed",
            complete=True,
            minutes_ago=5,
        )
        job_ids.append(job_id)
        run_ids.append(run_id)
        page = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/latest",
            headers=org1_user.headers,
        )
        rec = page.json()["items"][0]["recorded"]
        assert rec["outcome"] == "failed"
        assert rec["applicability"] == "applicable"
        assert rec["judge_mode"] == "exact"
        assert rec["case_version"] == 1

        # Revoking the source run omits the evaluation without failing.
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.commit()
        page = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/latest",
            headers=org1_user.headers,
        )
        assert page.json()["items"][0]["recorded"] is None
    finally:
        await _cleanup_latest(
            db_session, agent_id=agent_uuid, job_ids=job_ids, run_ids=run_ids
        )


async def test_latest_role_based_agent_denies_without_500(
    e2e_client, platform_admin, org1, org1_user, db_session: AsyncSession
):
    """The roles relationship must load; denial reads as 404, not 500."""
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Role Gate Agent {uuid4().hex[:8]}",
            "system_prompt": "Reply only with ok.",
            "channels": ["chat"],
            "access_level": "role_based",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["id"]
    try:
        page = e2e_client.get(
            f"/api/agent-evaluations/agents/{agent_id}/tests/latest",
            headers=org1_user.headers,
        )
        assert page.status_code == 404, page.text
    finally:
        e2e_client.delete(
            f"/api/agents/{agent_id}", headers=platform_admin.headers
        )
