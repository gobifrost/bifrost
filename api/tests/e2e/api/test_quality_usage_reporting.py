"""Public quality usage reporting API contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from shared.quality_usage import begin_quality_usage_attempt, record_quality_usage_observation
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_recorded_evaluations import AgentRecordedEvaluation
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.agents import Agent
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.platform_jobs import PlatformJob
from src.services.agent_runtime import types as runtime_types

pytestmark = pytest.mark.e2e


def _params(**overrides):
    values = {
        "start_date": "2026-09-20",
        "end_date": "2026-09-20",
        "limit": 50,
        "offset": 0,
    }
    values.update(overrides)
    return values


async def _seed_quality_ledger(db_session, *, org_id: UUID) -> tuple[UUID, UUID]:
    included_op = uuid4()
    excluded_op = uuid4()
    included_attempt = await begin_quality_usage_attempt(
        db_session,
        idempotency_key=f"e2e-public-included-{uuid4()}",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=included_op,
        quality_operation_item_id="case:run:0:hash",
        usage_purpose="recorded_semantic_judge",
        provider="openrouter",
        model="judge-v1",
        request_fingerprint="included-fingerprint",
        organization_id=org_id,
    )
    included_attempt.attempt.started_at = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    await db_session.flush()
    await record_quality_usage_observation(
        db_session,
        idempotency_key=included_attempt.attempt.idempotency_key,
        quality_operation_type="recorded_evaluation",
        quality_operation_id=included_op,
        quality_operation_item_id="case:run:0:hash",
        usage_purpose="recorded_semantic_judge",
        provider="openrouter",
        model="judge-v1",
        request_fingerprint="included-fingerprint",
        input_tokens=11,
        output_tokens=7,
        cache_read_tokens=2,
        cache_write_tokens=1,
        provider_cost=Decimal("0.00030000"),
    )
    gap_attempt = await begin_quality_usage_attempt(
        db_session,
        idempotency_key=f"e2e-public-gap-{uuid4()}",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=included_op,
        quality_operation_item_id="case:run:1:hash",
        usage_purpose="recorded_semantic_judge",
        provider="openrouter",
        model="judge-v1",
        request_fingerprint="gap-fingerprint",
        organization_id=org_id,
    )
    gap_attempt.attempt.started_at = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc)
    await db_session.flush()
    midnight_attempt = await begin_quality_usage_attempt(
        db_session,
        idempotency_key=f"e2e-public-midnight-{uuid4()}",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=excluded_op,
        usage_purpose="recorded_semantic_judge",
        provider="openrouter",
        model="judge-v1",
        request_fingerprint="midnight-fingerprint",
        organization_id=org_id,
    )
    midnight_attempt.attempt.started_at = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
    db_session.add(
        AIUsage(
            provider="openrouter",
            model="judge-v1",
            input_tokens=99,
            output_tokens=1,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider_cost=Decimal("0.00990000"),
            cost=Decimal("0.00990000"),
            quality_operation_type="recorded_evaluation",
            quality_operation_id=excluded_op,
            usage_purpose="recorded_semantic_judge",
            organization_id=org_id,
            timestamp=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
        )
    )
    await db_session.commit()
    return included_op, excluded_op


@pytest.mark.asyncio
async def test_usage_breakdown_http_auth_org_filters_decimal_json_and_date_window(
    e2e_client, platform_admin, org1_user, org1, db_session
):
    org_id = UUID(org1["id"])
    included_op, excluded_op = await _seed_quality_ledger(db_session, org_id=org_id)
    try:
        denied = e2e_client.get(
            "/api/reports/usage/breakdown",
            headers=org1_user.headers,
            params=_params(org_id=str(org_id)),
        )
        assert denied.status_code == 403, denied.text

        response = e2e_client.get(
            "/api/reports/usage/breakdown",
            headers=platform_admin.headers,
            params=_params(org_id=str(org_id), provider="openrouter"),
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["overall"]["input_tokens"] == 11
        assert payload["overall"]["known_cost"] == "0.00030000"
        assert payload["coverage"]["started_attempt_count"] == 1
        assert payload["by_provider_model"]["items"][0]["provider"] == "openrouter"

        reversed_dates = e2e_client.get(
            "/api/reports/usage/breakdown",
            headers=platform_admin.headers,
            params={"start_date": "2026-09-21", "end_date": "2026-09-20"},
        )
        assert reversed_dates.status_code == 422
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(AIUsage).where(AIUsage.quality_operation_id.in_([included_op, excluded_op]))
        )
        await db_session.execute(
            delete(AIUsageAttempt).where(AIUsageAttempt.quality_operation_id.in_([included_op, excluded_op]))
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_usage_breakdown_http_global_only_filters_null_org_attempts_and_usage(
    e2e_client, platform_admin, org1, db_session
):
    org_id = UUID(org1["id"])
    global_op = uuid4()
    tenant_op = uuid4()
    db_session.add_all(
        [
            AIUsage(
                provider="openrouter",
                model="judge-v1",
                input_tokens=13,
                output_tokens=8,
                cache_read_tokens=0,
                cache_write_tokens=0,
                provider_cost=Decimal("0.00070000"),
                cost=Decimal("0.00070000"),
                quality_operation_type="recorded_evaluation",
                quality_operation_id=global_op,
                usage_purpose="recorded_semantic_judge",
                organization_id=None,
                timestamp=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
            ),
            AIUsage(
                provider="openrouter",
                model="judge-v1",
                input_tokens=99,
                output_tokens=9,
                cache_read_tokens=0,
                cache_write_tokens=0,
                provider_cost=Decimal("0.00990000"),
                cost=Decimal("0.00990000"),
                quality_operation_type="recorded_evaluation",
                quality_operation_id=tenant_op,
                usage_purpose="recorded_semantic_judge",
                organization_id=org_id,
                timestamp=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
            ),
            AIUsageAttempt(
                idempotency_key=f"e2e-global-started-{uuid4()}",
                quality_operation_type="recorded_evaluation",
                quality_operation_id=global_op,
                usage_purpose="recorded_semantic_judge",
                provider="openrouter",
                model="judge-v1",
                request_fingerprint=f"global-gap-{uuid4()}",
                state="started",
                organization_id=None,
                started_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
            ),
            AIUsageAttempt(
                idempotency_key=f"e2e-tenant-started-{uuid4()}",
                quality_operation_type="recorded_evaluation",
                quality_operation_id=tenant_op,
                usage_purpose="recorded_semantic_judge",
                provider="openrouter",
                model="judge-v1",
                request_fingerprint=f"tenant-gap-{uuid4()}",
                state="started",
                organization_id=org_id,
                started_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
            ),
        ]
    )
    await db_session.commit()
    try:
        conflict = e2e_client.get(
            "/api/reports/usage/breakdown",
            headers=platform_admin.headers,
            params=_params(org_id=str(org_id), global_only=True),
        )
        assert conflict.status_code == 422, conflict.text

        response = e2e_client.get(
            "/api/reports/usage/breakdown",
            headers=platform_admin.headers,
            params=_params(global_only=True),
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["overall"]["input_tokens"] == 13
        assert payload["overall"]["known_cost"] == "0.00070000"
        assert payload["coverage"]["started_attempt_count"] == 1
        assert payload["by_organization"]["items"][0]["organization_id"] is None
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(AIUsage).where(AIUsage.quality_operation_id.in_([global_op, tenant_op]))
        )
        await db_session.execute(
            delete(AIUsageAttempt).where(
                AIUsageAttempt.quality_operation_id.in_([global_op, tenant_op])
            )
        )
        await db_session.commit()


async def _seed_recorded_usage_eval(db_session, *, org_id: UUID, requester_id: UUID):
    agent = Agent(
        id=uuid4(),
        name=f"Recorded Usage {uuid4().hex[:8]}",
        system_prompt="Test only.",
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=org_id,
        created_by="test",
    )
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        org_id=org_id,
        agent_id=agent.id,
        trigger_type="api",
        status="completed",
        root_run_id=run_id,
        output={"ok": True},
        caller_user_id=str(requester_id),
    )
    journal = AgentRunJournalEntry(
        run_id=run_id,
        sequence=1,
        kind=runtime_types.JOURNAL_COMPLETION,
        data={"status": "completed"},
    )
    job = PlatformJob(
        id=uuid4(),
        job_type="agent.evaluation_recorded_semantic",
        payload_version=1,
        payload={},
        organization_id=org_id,
        requested_by_user_id=str(requester_id),
        requested_by_email="requester@example.com",
        requested_by_name="Usage Test Requester",
        title="Recorded usage",
        status="succeeded",
        resource_type="agent_recorded_evaluation",
        resource_id=str(uuid4()),
    )
    evaluation = AgentRecordedEvaluation(
        id=uuid4(),
        org_id=org_id,
        agent_id=agent.id,
        requested_by_user_id=str(requester_id),
        requested_by_email="requester@example.com",
        platform_job_id=job.id,
        frozen_input={
            "judge_mode": "semantic",
            "cases": [],
            "runs": [
                {
                    "run_id": str(run_id),
                    "source_runs": [
                        {"run_id": str(run_id), "agent_id": str(agent.id), "org_id": str(org_id)}
                    ],
                }
            ],
            "applicability": {},
        },
    )
    db_session.add(agent)
    await db_session.flush()
    db_session.add_all([run, job])
    await db_session.flush()
    db_session.add_all([journal, evaluation])
    await db_session.flush()
    await record_quality_usage_observation(
        db_session,
        idempotency_key=(
            await begin_quality_usage_attempt(
                db_session,
                idempotency_key=f"e2e-recorded-usage-{uuid4()}",
                quality_operation_type="recorded_evaluation",
                quality_operation_id=evaluation.id,
                usage_purpose="recorded_semantic_judge",
                provider="openai",
                model="judge-v1",
                request_fingerprint="recorded-fingerprint",
                organization_id=org_id,
                user_id=requester_id,
                platform_job_id=job.id,
            )
        ).attempt.idempotency_key,
        quality_operation_type="recorded_evaluation",
        quality_operation_id=evaluation.id,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="judge-v1",
        request_fingerprint="recorded-fingerprint",
        input_tokens=5,
        output_tokens=4,
        provider_cost=Decimal("0.00010000"),
    )
    await db_session.commit()
    return evaluation.id, job.id, run_id, agent.id


@pytest.mark.asyncio
async def test_recorded_usage_http_enforces_requester_and_current_source_authorization(
    e2e_client, org1_user, org2_user, org1, org2, db_session
):
    org_id = UUID(org1["id"])
    evaluation_id, job_id, run_id, agent_id = await _seed_recorded_usage_eval(
        db_session, org_id=org_id, requester_id=org1_user.user_id
    )
    try:
        authorized = e2e_client.get(
            f"/api/agent-evaluations/recorded-evaluations/{evaluation_id}/usage",
            headers=org1_user.headers,
        )
        assert authorized.status_code == 200, authorized.text
        payload = authorized.json()
        assert payload["overall"]["input_tokens"] == 5
        assert payload["overall"]["known_cost"] == "0.00010000"

        other_user = e2e_client.get(
            f"/api/agent-evaluations/recorded-evaluations/{evaluation_id}/usage",
            headers=org2_user.headers,
        )
        assert other_user.status_code == 404

        run = await db_session.get(AgentRun, run_id)
        run.org_id = UUID(org2["id"])
        await db_session.commit()
        revoked = e2e_client.get(
            f"/api/agent-evaluations/recorded-evaluations/{evaluation_id}/usage",
            headers=org1_user.headers,
        )
        assert revoked.status_code == 404
    finally:
        await db_session.rollback()
        await db_session.execute(delete(AIUsage).where(AIUsage.quality_operation_id == evaluation_id))
        await db_session.execute(delete(AIUsageAttempt).where(AIUsageAttempt.quality_operation_id == evaluation_id))
        await db_session.execute(delete(AgentRecordedEvaluation).where(AgentRecordedEvaluation.id == evaluation_id))
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
        await db_session.execute(delete(AgentRunJournalEntry).where(AgentRunJournalEntry.run_id == run_id))
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.execute(delete(Agent).where(Agent.id == agent_id))
        await db_session.commit()
