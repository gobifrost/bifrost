"""Synthetic and designer operation usage reporting API contracts."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from src.models.enums import AgentAccessLevel
from src.models.orm.agent_evaluations import AgentEvaluationExecution, AgentEvaluationSuite
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.services.agent_evaluations.runner import build_synthetic_correlation

pytestmark = pytest.mark.e2e


def _usage(**overrides) -> AIUsage:
    values = {
        "provider": "openai",
        "model": "gpt-test",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "provider_cost": Decimal("0.00010000"),
        "cost": Decimal("0.00010000"),
        "sequence": 1,
    }
    values.update(overrides)
    return AIUsage(**values)


async def _seed_agent(db_session, *, org_id, owner_user_id, access_level=AgentAccessLevel.AUTHENTICATED):
    agent = Agent(
        id=uuid4(),
        name=f"usage-agent-{uuid4().hex[:8]}",
        system_prompt="Test only.",
        access_level=access_level,
        organization_id=org_id,
        owner_user_id=owner_user_id,
        created_by="test",
        is_active=True,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


async def _seed_suite_execution(db_session, *, org_id, owner_user_id):
    agent = await _seed_agent(db_session, org_id=org_id, owner_user_id=owner_user_id)
    suite = AgentEvaluationSuite(
        id=uuid4(),
        org_id=org_id,
        agent_id=agent.id,
        name=f"usage-suite-{uuid4().hex[:8]}",
        status="published",
        version=1,
        created_by="test",
    )
    db_session.add(suite)
    await db_session.flush()
    execution = AgentEvaluationExecution(
        id=uuid4(),
        suite_id=suite.id,
        suite_version=suite.version,
        baseline_agent_id=agent.id,
        baseline_snapshot={"agent_id": str(agent.id), "evaluation": {"mode": "evaluation_synthetic"}},
        status="succeeded",
        total_cases=1,
    )
    db_session.add(execution)
    await db_session.flush()
    return agent, suite, execution


async def _cleanup(
    db_session,
    *,
    usage_operation_ids=(),
    attempt_operation_ids=(),
    run_ids=(),
    execution_ids=(),
    suite_ids=(),
    agent_ids=(),
):
    await db_session.rollback()
    if usage_operation_ids:
        await db_session.execute(
            delete(AIUsage).where(AIUsage.quality_operation_id.in_(usage_operation_ids))
        )
    if attempt_operation_ids:
        await db_session.execute(
            delete(AIUsageAttempt).where(
                AIUsageAttempt.quality_operation_id.in_(attempt_operation_ids)
            )
        )
    if run_ids:
        await db_session.execute(delete(AIUsage).where(AIUsage.agent_run_id.in_(run_ids)))
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
    if execution_ids:
        await db_session.execute(
            delete(AgentEvaluationExecution).where(
                AgentEvaluationExecution.id.in_(execution_ids)
            )
        )
    if suite_ids:
        await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id.in_(suite_ids)))
    if agent_ids:
        await db_session.execute(delete(Agent).where(Agent.id.in_(agent_ids)))
    await db_session.commit()


@pytest.mark.asyncio
async def test_synthetic_execution_usage_reports_source_and_judge_operation_isolated(
    e2e_client, org1_user, db_session
):
    org_id = org1_user.organization_id
    agent, suite, execution = await _seed_suite_execution(
        db_session, org_id=org_id, owner_user_id=org1_user.user_id
    )
    _other_agent, other_suite, other_execution = await _seed_suite_execution(
        db_session, org_id=org_id, owner_user_id=org1_user.user_id
    )
    source_run = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        org_id=org_id,
        trigger_type="evaluation_synthetic",
        status="completed",
        root_run_id=None,
        correlation=build_synthetic_correlation(
            suite_id=suite.id,
            case_id=uuid4(),
            execution_id=execution.id,
            side="baseline",
        ),
    )
    db_session.add(source_run)
    await db_session.flush()
    db_session.add_all([
        _usage(
            agent_run_id=source_run.id,
            organization_id=org_id,
            input_tokens=11,
            output_tokens=2,
            provider="openai",
            model="source-model",
            provider_cost=Decimal("0.00011000"),
            cost=Decimal("0.00011000"),
        ),
        _usage(
            organization_id=org_id,
            input_tokens=7,
            output_tokens=3,
            provider="openrouter",
            model="judge-model",
            provider_cost=Decimal("0.00007000"),
            cost=Decimal("0.00007000"),
            quality_operation_type="synthetic_evaluation",
            quality_operation_id=execution.id,
            usage_purpose="synthetic_semantic_judge",
        ),
        _usage(
            organization_id=org_id,
            input_tokens=100,
            output_tokens=30,
            quality_operation_type="synthetic_evaluation",
            quality_operation_id=other_execution.id,
            usage_purpose="synthetic_semantic_judge",
        ),
    ])
    await db_session.commit()
    try:
        before_rows = (
            await db_session.execute(
                select(func.count()).select_from(AIUsage).where(
                    AIUsage.quality_operation_id == execution.id
                )
            )
        ).scalar_one()
        response = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}/usage",
            headers=org1_user.headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["overall"]["input_tokens"] == 18
        assert payload["coverage"]["legacy_coverage_unknown"] is True
        purposes = {item["purpose"] for item in payload["by_purpose"]["items"]}
        assert {"simulation", "synthetic_semantic_judge"} <= purposes
        providers = {(item["provider"], item["model"]) for item in payload["by_provider_model"]["items"]}
        assert ("openai", "source-model") in providers
        assert ("openrouter", "judge-model") in providers
        after_rows = (
            await db_session.execute(
                select(func.count()).select_from(AIUsage).where(
                    AIUsage.quality_operation_id == execution.id
                )
            )
        ).scalar_one()
        assert after_rows == before_rows
    finally:
        await _cleanup(
            db_session,
            usage_operation_ids=[execution.id, other_execution.id],
            run_ids=[source_run.id],
            execution_ids=[execution.id, other_execution.id],
            suite_ids=[suite.id, other_suite.id],
            agent_ids=[agent.id, _other_agent.id],
        )


@pytest.mark.asyncio
async def test_synthetic_execution_usage_zero_rows_keeps_coverage_unknown(
    e2e_client, org1_user, db_session
):
    agent, suite, execution = await _seed_suite_execution(
        db_session, org_id=org1_user.organization_id, owner_user_id=org1_user.user_id
    )
    await db_session.commit()
    try:
        response = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}/usage",
            headers=org1_user.headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["overall"]["call_count"] == 0
        assert payload["coverage"]["legacy_coverage_unknown"] is True
    finally:
        await _cleanup(
            db_session,
            execution_ids=[execution.id],
            suite_ids=[suite.id],
            agent_ids=[agent.id],
        )


async def _seed_designer_run(
    db_session,
    *,
    org_id,
    requester_id,
    agent=None,
    suite=None,
    correlation_overrides=None,
    root_run_id="self",
    parent_run_id=None,
    raw_correlation=None,
):
    if agent is None:
        agent = await _seed_agent(db_session, org_id=org_id, owner_user_id=requester_id)
    if suite is None:
        suite = AgentEvaluationSuite(
            id=uuid4(),
            org_id=org_id,
            agent_id=agent.id,
            name=f"designer-suite-{uuid4().hex[:8]}",
            status="draft",
            version=1,
            created_by="test",
        )
        db_session.add(suite)
        await db_session.flush()
    run_id = uuid4()
    correlation = {
        **build_synthetic_correlation(
            suite_id=suite.id,
            case_id=uuid4(),
            execution_id=uuid4(),
            side="baseline",
        ),
        "evaluation_designer": True,
        "designer_suite_id": str(suite.id),
    }
    if correlation_overrides:
        correlation.update(correlation_overrides)
    if raw_correlation is not None:
        correlation = raw_correlation
    run = AgentRun(
        id=run_id,
        agent_id=agent.id,
        org_id=org_id,
        trigger_type="evaluation_synthetic",
        status="completed",
        root_run_id=run_id if root_run_id == "self" else root_run_id,
        parent_run_id=parent_run_id,
        caller_user_id=str(requester_id),
        correlation=correlation,
    )
    db_session.add(run)
    await db_session.flush()
    return agent, suite, run


@pytest.mark.asyncio
async def test_designer_run_usage_authorizes_root_original_agent_and_reports_unknown_coverage(
    e2e_client, org1_user, db_session
):
    agent, suite, run = await _seed_designer_run(
        db_session, org_id=org1_user.organization_id, requester_id=org1_user.user_id
    )
    db_session.add(
        _usage(
            agent_run_id=run.id,
            organization_id=org1_user.organization_id,
            input_tokens=13,
            output_tokens=4,
            provider="anthropic",
            model="designer-model",
            provider_cost=Decimal("0.00013000"),
            cost=Decimal("0.00013000"),
        )
    )
    await db_session.commit()
    try:
        response = e2e_client.get(
            f"/api/agent-evaluations/designer-runs/{run.id}/usage",
            headers=org1_user.headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["overall"]["input_tokens"] == 13
        assert payload["coverage"]["legacy_coverage_unknown"] is True
        assert payload["by_purpose"]["items"][0]["purpose"] == "test_designer"
    finally:
        await _cleanup(
            db_session,
            run_ids=[run.id],
            suite_ids=[suite.id],
            agent_ids=[agent.id],
        )


@pytest.mark.asyncio
async def test_designer_run_usage_rejects_child_string_flag_null_org_mismatch_and_private_original(
    e2e_client, org1_user, bob_user, db_session
):
    visible_agent = await _seed_agent(
        db_session, org_id=org1_user.organization_id, owner_user_id=org1_user.user_id
    )
    private_agent = await _seed_agent(
        db_session,
        org_id=org1_user.organization_id,
        owner_user_id=bob_user.user_id,
        access_level=AgentAccessLevel.PRIVATE,
    )
    suite = AgentEvaluationSuite(
        id=uuid4(),
        org_id=org1_user.organization_id,
        agent_id=visible_agent.id,
        name=f"designer-deny-{uuid4().hex[:8]}",
        status="draft",
        version=1,
        created_by="test",
    )
    db_session.add(suite)
    await db_session.flush()
    _agent, _suite, valid_root = await _seed_designer_run(
        db_session,
        org_id=org1_user.organization_id,
        requester_id=org1_user.user_id,
        agent=visible_agent,
        suite=suite,
    )
    _agent, _suite, child = await _seed_designer_run(
        db_session,
        org_id=org1_user.organization_id,
        requester_id=org1_user.user_id,
        agent=visible_agent,
        suite=suite,
        root_run_id=valid_root.id,
        parent_run_id=valid_root.id,
    )
    _agent, _suite, parent_child = await _seed_designer_run(
        db_session,
        org_id=org1_user.organization_id,
        requester_id=org1_user.user_id,
        agent=visible_agent,
        suite=suite,
        parent_run_id=child.id,
    )
    _agent, _suite, malformed_correlation = await _seed_designer_run(
        db_session,
        org_id=org1_user.organization_id,
        requester_id=org1_user.user_id,
        agent=visible_agent,
        suite=suite,
        raw_correlation=["not", "a", "mapping"],
    )
    _agent, _suite, string_flag = await _seed_designer_run(
        db_session,
        org_id=org1_user.organization_id,
        requester_id=org1_user.user_id,
        agent=visible_agent,
        suite=suite,
        correlation_overrides={"evaluation_designer": "true"},
    )
    _agent, _suite, null_org = await _seed_designer_run(
        db_session,
        org_id=None,
        requester_id=org1_user.user_id,
        agent=visible_agent,
        suite=suite,
    )
    _agent, _suite, private_original = await _seed_designer_run(
        db_session,
        org_id=org1_user.organization_id,
        requester_id=org1_user.user_id,
        agent=private_agent,
        suite=suite,
    )
    await db_session.commit()
    try:
        for run in (
            child,
            parent_child,
            malformed_correlation,
            string_flag,
            null_org,
            private_original,
        ):
            response = e2e_client.get(
                f"/api/agent-evaluations/designer-runs/{run.id}/usage",
                headers=org1_user.headers,
            )
            assert response.status_code == 404, response.text
    finally:
        await _cleanup(
            db_session,
            run_ids=[
                valid_root.id,
                child.id,
                parent_child.id,
                malformed_correlation.id,
                string_flag.id,
                null_org.id,
                private_original.id,
            ],
            suite_ids=[suite.id],
            agent_ids=[visible_agent.id, private_agent.id],
        )
