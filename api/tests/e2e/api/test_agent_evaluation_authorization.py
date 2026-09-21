"""Studio reference authorization uses live grants and run visibility."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.agent_evaluations import (
    AgentEvaluationExecution,
    AgentEvaluationMatrix,
    AgentEvaluationSuite,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_models import AIModelProfile
from src.routers.agent_evaluations import _execution_and_suite_or_404

pytestmark = pytest.mark.asyncio


async def test_studio_honors_new_role_grant_without_reissuing_user_token(
    e2e_client, platform_admin, alice_user, db_session,
):
    role = e2e_client.post("/api/roles", headers=platform_admin.headers,
                           json={"name": f"Studio role {uuid4().hex}"})
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]
    agent_id = None
    suite_id = None
    try:
        agent = e2e_client.post("/api/agents", headers=platform_admin.headers, json={
            "name": f"Studio role agent {uuid4().hex}", "system_prompt": "Test only",
            "access_level": "role_based", "role_ids": [role_id],
            "organization_id": str(alice_user.organization_id),
        })
        assert agent.status_code == 201, agent.text
        agent_id = agent.json()["id"]
        body = {"name": f"Role suite {uuid4().hex}", "agent_id": agent_id}
        denied = e2e_client.post("/api/agent-evaluations/suites", headers=alice_user.headers, json=body)
        assert denied.status_code == 404
        assigned = e2e_client.post(f"/api/roles/{role_id}/users", headers=platform_admin.headers,
                                  json={"user_ids": [str(alice_user.user_id)]})
        assert assigned.status_code == 204, assigned.text
        allowed = e2e_client.post("/api/agent-evaluations/suites", headers=alice_user.headers, json=body)
        assert allowed.status_code == 200, allowed.text
        suite_id = UUID(allowed.json()["id"])
    finally:
        if suite_id is not None:
            await db_session.execute(delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id == suite_id))
            await db_session.commit()
        if agent_id is not None:
            e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)
        e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)


async def test_designer_cannot_read_another_users_private_delegation(
    e2e_client, alice_user, bob_user, db_session,
):
    suite = AgentEvaluationSuite(
        name=f"Private history {uuid4().hex}", org_id=alice_user.organization_id,
        status="draft", version=1,
    )
    run = AgentRun(
        status="completed", trigger_type="delegation", org_id=alice_user.organization_id,
        caller_user_id=str(bob_user.user_id), input={"task": "private chat"},
    )
    db_session.add_all([suite, run])
    await db_session.commit()
    try:
        result = e2e_client.post(
            f"/api/agent-evaluations/suites/{suite.id}/designer/drafts",
            headers=alice_user.headers,
            json={"suite_goal": "test", "historical_run_ids": [str(run.id)]},
        )
        assert result.status_code == 404, result.text
    finally:
        await db_session.delete(run)
        await db_session.delete(suite)
        await db_session.commit()


async def test_candidate_cannot_select_an_ungranted_model_profile(
    e2e_client, platform_admin, alice_user, db_session,
):
    headers = platform_admin.headers
    connection = e2e_client.post("/api/admin/ai/connections", headers=headers, json={
        "name": f"Private judge {uuid4().hex}", "provider": "openai_compatible",
        "endpoint": "http://scheduler-fixtures:8080/v1", "api_key": "fixture-key",
    })
    assert connection.status_code == 201, connection.text
    connection_id = connection.json()["id"]
    profile_id = agent_id = None
    try:
        # Seed an unassigned private profile without the first-profile setup
        # flow, which intentionally enables chat and creates assignments.
        profile = AIModelProfile(
            name=f"Private judge {uuid4().hex}", connection_id=UUID(connection_id),
            model="private-model", enabled_for_chat=False,
        )
        db_session.add(profile)
        await db_session.commit()
        profile_id = str(profile.id)
        agent = e2e_client.post("/api/agents", headers=headers, json={
            "name": f"Visible base {uuid4().hex}", "system_prompt": "Test only",
            "access_level": "authenticated",
            "organization_id": str(alice_user.organization_id),
        })
        assert agent.status_code == 201, agent.text
        agent_id = agent.json()["id"]
        candidate = e2e_client.post("/api/agent-evaluations/candidates", headers=alice_user.headers,
                                    json={"base_agent_id": agent_id, "overlays": {"llm_profile_id": profile_id}})
        assert candidate.status_code == 403, candidate.text
    finally:
        if agent_id is not None:
            e2e_client.delete(f"/api/agents/{agent_id}", headers=headers).raise_for_status()
        if profile_id is not None:
            e2e_client.delete(f"/api/admin/ai/profiles/{profile_id}", headers=headers).raise_for_status()
        e2e_client.delete(f"/api/admin/ai/connections/{connection_id}", headers=headers).raise_for_status()

async def _seed_synthetic_execution(
    db_session,
    *,
    org_id,
    agent_owner_id,
    access_level=AgentAccessLevel.PRIVATE,
    status="running",
    matrix_id=None,
):
    agent = Agent(
        name=f"authz-agent-{uuid4().hex}",
        system_prompt="Test only",
        access_level=access_level,
        organization_id=org_id,
        owner_user_id=agent_owner_id,
        created_by="test",
    )
    db_session.add(agent)
    await db_session.flush()
    suite = AgentEvaluationSuite(
        name=f"authz-suite-{uuid4().hex}",
        org_id=org_id,
        agent_id=agent.id,
        status="published",
        version=1,
        created_by="test",
    )
    db_session.add(suite)
    await db_session.flush()
    execution = AgentEvaluationExecution(
        suite_id=suite.id,
        suite_version=suite.version,
        baseline_agent_id=agent.id,
        baseline_snapshot={
            "agent_id": str(agent.id),
            "evaluation": {"mode": "evaluation_synthetic"},
        },
        status=status,
        matrix_id=matrix_id,
    )
    db_session.add(execution)
    await db_session.flush()
    return agent, suite, execution


async def _cleanup_authz_rows(
    db_session, *, execution_ids=(), matrix_ids=(), suite_ids=(), agent_ids=()
):
    from src.models.orm.agent_evaluations import AgentEvaluationResult

    if execution_ids:
        await db_session.execute(
            delete(AgentEvaluationResult).where(
                AgentEvaluationResult.execution_id.in_(execution_ids)
            )
        )
        await db_session.execute(
            delete(AgentEvaluationExecution).where(AgentEvaluationExecution.id.in_(execution_ids))
        )
    if matrix_ids:
        await db_session.execute(
            delete(AgentEvaluationMatrix).where(AgentEvaluationMatrix.id.in_(matrix_ids))
        )
    if suite_ids:
        await db_session.execute(
            delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id.in_(suite_ids))
        )
    if agent_ids:
        await db_session.execute(delete(Agent).where(Agent.id.in_(agent_ids)))
    await db_session.commit()


async def test_synthetic_execution_read_results_cancel_require_live_baseline_access(
    e2e_client, alice_user, bob_user, db_session,
):
    agent, suite, execution = await _seed_synthetic_execution(
        db_session,
        org_id=alice_user.organization_id,
        agent_owner_id=alice_user.user_id,
    )
    await db_session.commit()
    try:
        get_denied = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}",
            headers=bob_user.headers,
        )
        assert get_denied.status_code == 404, get_denied.text
        results_denied = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}/results",
            headers=bob_user.headers,
        )
        assert results_denied.status_code == 404, results_denied.text
        cancel_denied = e2e_client.post(
            f"/api/agent-evaluations/executions/{execution.id}/cancel",
            headers=bob_user.headers,
        )
        assert cancel_denied.status_code == 404, cancel_denied.text
        await db_session.refresh(execution)
        assert execution.status == "running"

        get_allowed = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}",
            headers=alice_user.headers,
        )
        assert get_allowed.status_code == 200, get_allowed.text
        cancel_allowed = e2e_client.post(
            f"/api/agent-evaluations/executions/{execution.id}/cancel",
            headers=alice_user.headers,
        )
        assert cancel_allowed.status_code == 200, cancel_allowed.text
        assert cancel_allowed.json()["status"] == "cancelled"
    finally:
        await _cleanup_authz_rows(
            db_session,
            execution_ids=[execution.id],
            suite_ids=[suite.id],
            agent_ids=[agent.id],
        )


async def test_synthetic_execution_deleted_baseline_fails_closed_for_non_admin(
    e2e_client, platform_admin, alice_user, db_session,
):
    agent, suite, execution = await _seed_synthetic_execution(
        db_session,
        org_id=alice_user.organization_id,
        agent_owner_id=alice_user.user_id,
        access_level=AgentAccessLevel.AUTHENTICATED,
    )
    execution.baseline_agent_id = None
    await db_session.commit()
    try:
        admin_get = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}",
            headers=platform_admin.headers,
        )
        assert admin_get.status_code == 200, admin_get.text
        user_get = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}",
            headers=alice_user.headers,
        )
        assert user_get.status_code == 404, user_get.text
        user_results = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}/results",
            headers=alice_user.headers,
        )
        assert user_results.status_code == 404, user_results.text
        user_cancel = e2e_client.post(
            f"/api/agent-evaluations/executions/{execution.id}/cancel",
            headers=alice_user.headers,
        )
        assert user_cancel.status_code == 404, user_cancel.text
        await db_session.refresh(execution)
        assert execution.status == "running"
    finally:
        await _cleanup_authz_rows(
            db_session,
            execution_ids=[execution.id],
            suite_ids=[suite.id],
            agent_ids=[agent.id],
        )


async def test_synthetic_global_execution_denies_non_admin_even_with_live_global_agent(
    e2e_client, platform_admin, alice_user, db_session,
):
    agent, suite, execution = await _seed_synthetic_execution(
        db_session,
        org_id=None,
        agent_owner_id=None,
        access_level=AgentAccessLevel.AUTHENTICATED,
    )
    await db_session.commit()
    try:
        denied = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}",
            headers=alice_user.headers,
        )
        assert denied.status_code == 404, denied.text
        admin_get = e2e_client.get(
            f"/api/agent-evaluations/executions/{execution.id}",
            headers=platform_admin.headers,
        )
        assert admin_get.status_code == 200, admin_get.text
    finally:
        await _cleanup_authz_rows(
            db_session,
            execution_ids=[execution.id],
            suite_ids=[suite.id],
            agent_ids=[agent.id],
        )


async def test_null_org_principal_cannot_read_null_org_execution_but_admin_can(
    db_session, platform_admin,
):
    agent, suite, execution = await _seed_synthetic_execution(
        db_session,
        org_id=None,
        agent_owner_id=None,
        access_level=AgentAccessLevel.AUTHENTICATED,
    )
    await db_session.commit()
    null_org_user = UserPrincipal(
        user_id=uuid4(),
        email="invalid-null-org@example.com",
        organization_id=None,
        is_superuser=False,
    )
    admin = UserPrincipal(
        user_id=platform_admin.user_id,
        email=platform_admin.email,
        organization_id=platform_admin.organization_id,
        is_superuser=True,
    )
    try:
        with pytest.raises(Exception) as excinfo:
            await _execution_and_suite_or_404(db_session, null_org_user, execution.id)
        assert getattr(excinfo.value, "status_code", None) == 404

        allowed_execution, allowed_suite = await _execution_and_suite_or_404(
            db_session, admin, execution.id
        )
        assert allowed_execution.id == execution.id
        assert allowed_suite.id == suite.id
    finally:
        await _cleanup_authz_rows(
            db_session,
            execution_ids=[execution.id],
            suite_ids=[suite.id],
            agent_ids=[agent.id],
        )


async def test_batch_missing_member_fails_closed_before_mutation(
    e2e_client, alice_user, db_session,
):
    agent, suite, execution = await _seed_synthetic_execution(
        db_session,
        org_id=alice_user.organization_id,
        agent_owner_id=alice_user.user_id,
    )
    matrix = AgentEvaluationMatrix(
        suite_id=suite.id,
        suite_version=suite.version,
        org_id=alice_user.organization_id,
        cell_execution_ids=[str(execution.id), str(uuid4())],
        created_by="test",
    )
    db_session.add(matrix)
    execution.matrix_id = matrix.id
    await db_session.commit()
    try:
        denied_get = e2e_client.get(
            f"/api/agent-evaluations/executions/batch/{matrix.id}",
            headers=alice_user.headers,
        )
        assert denied_get.status_code == 404, denied_get.text
        denied_cancel = e2e_client.post(
            f"/api/agent-evaluations/executions/batch/{matrix.id}/cancel",
            headers=alice_user.headers,
        )
        assert denied_cancel.status_code == 404, denied_cancel.text
        await db_session.refresh(execution)
        assert execution.status == "running"
    finally:
        await _cleanup_authz_rows(
            db_session,
            execution_ids=[execution.id],
            matrix_ids=[matrix.id],
            suite_ids=[suite.id],
            agent_ids=[agent.id],
        )


async def test_batch_cancel_authorizes_all_members_before_mutation(
    e2e_client, platform_admin, alice_user, bob_user, db_session,
):
    alice_agent, suite, alice_execution = await _seed_synthetic_execution(
        db_session,
        org_id=alice_user.organization_id,
        agent_owner_id=alice_user.user_id,
    )
    matrix = AgentEvaluationMatrix(
        suite_id=suite.id,
        suite_version=1,
        org_id=alice_user.organization_id,
        cell_execution_ids=[],
        created_by="test",
    )
    db_session.add(matrix)
    await db_session.flush()
    alice_execution.matrix_id = matrix.id
    bob_agent = Agent(
        name=f"authz-agent-{uuid4().hex}",
        system_prompt="Test only",
        access_level=AgentAccessLevel.PRIVATE,
        organization_id=alice_user.organization_id,
        owner_user_id=bob_user.user_id,
        created_by="test",
    )
    db_session.add(bob_agent)
    await db_session.flush()
    bob_execution = AgentEvaluationExecution(
        suite_id=suite.id,
        suite_version=suite.version,
        baseline_agent_id=bob_agent.id,
        baseline_snapshot={
            "agent_id": str(bob_agent.id),
            "evaluation": {"mode": "evaluation_synthetic"},
        },
        status="running",
        matrix_id=matrix.id,
    )
    db_session.add(bob_execution)
    await db_session.flush()
    matrix.cell_execution_ids = [str(alice_execution.id), str(bob_execution.id)]
    await db_session.commit()
    try:
        denied_get = e2e_client.get(
            f"/api/agent-evaluations/executions/batch/{matrix.id}",
            headers=alice_user.headers,
        )
        assert denied_get.status_code == 404, denied_get.text
        denied_cancel = e2e_client.post(
            f"/api/agent-evaluations/executions/batch/{matrix.id}/cancel",
            headers=alice_user.headers,
        )
        assert denied_cancel.status_code == 404, denied_cancel.text
        await db_session.refresh(alice_execution)
        await db_session.refresh(bob_execution)
        assert alice_execution.status == "running"
        assert bob_execution.status == "running"

        admin_cancel = e2e_client.post(
            f"/api/agent-evaluations/executions/batch/{matrix.id}/cancel",
            headers=platform_admin.headers,
        )
        assert admin_cancel.status_code == 200, admin_cancel.text
        await db_session.refresh(alice_execution)
        await db_session.refresh(bob_execution)
        assert alice_execution.status == "cancelled"
        assert bob_execution.status == "cancelled"
    finally:
        await _cleanup_authz_rows(
            db_session,
            execution_ids=[alice_execution.id, bob_execution.id],
            matrix_ids=[matrix.id],
            suite_ids=[suite.id],
            agent_ids=[alice_agent.id, bob_agent.id],
        )
