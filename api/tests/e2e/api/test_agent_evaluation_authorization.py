"""Studio reference authorization uses live grants and run visibility."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.agent_evaluations import AgentEvaluationSuite
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_models import AIModelProfile

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
