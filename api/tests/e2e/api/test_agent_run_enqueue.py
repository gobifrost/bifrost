from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import delete

from src.services.ai_model_service import AIModelService
from tests.helpers.polling import poll_until
from src.models.orm.agents import Agent
from src.models.orm.ai_models import AIModelProfile, AIProviderConnection

import pytest


pytestmark = pytest.mark.asyncio


async def test_enqueue_returns_immediately_queryable_run(
    e2e_client,
    platform_admin,
    db_session,
):
    name = f"Queued Agent {uuid4().hex[:8]}"
    connection = AIProviderConnection(
        name=f"Enqueue test {uuid4().hex}", provider="openai",
        endpoint="http://127.0.0.1:9/v1", encrypted_api_key=AIModelService(db_session).encrypt_api_key("test-only"),
    )
    db_session.add(connection)
    await db_session.flush()
    profile = AIModelProfile(
        name=f"Enqueue test {uuid4().hex}", connection_id=connection.id,
        model="gpt-4o-mini", enabled_for_chat=False,
    )
    db_session.add(profile)
    await db_session.commit()
    agent_id = None
    run_id = None
    try:
        created = e2e_client.post(
            "/api/agents",
            json={
                "name": name,
                "description": "Agent enqueue lifecycle test",
                "system_prompt": "Return a short acknowledgement.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "max_run_timeout": 5,
                "llm_profile_id": str(profile.id),
            },
            headers=platform_admin.headers,
        )
        assert created.status_code == 201, created.text
        agent_id = UUID(created.json()["id"])

        accepted = e2e_client.post(
            "/api/agent-runs/enqueue",
            json={"agent_name": name, "input": {"ticket_id": 42}},
            headers=platform_admin.headers,
        )

        assert accepted.status_code == 202, accepted.text
        receipt = accepted.json()
        run_id = receipt["run_id"]
        assert receipt["status"] == "queued"

        fetched = e2e_client.get(
            f"/api/agent-runs/{receipt['run_id']}",
            headers=platform_admin.headers,
        )
        assert fetched.status_code == 200, fetched.text
        run = fetched.json()
        assert run["id"] == receipt["run_id"]
        assert run["agent_name"] == name
        assert run["status"] in {
            "queued",
            "running",
            "completed",
            "failed",
            "timeout",
        }
    finally:
        if run_id is not None:
            # The test owns a real queued job. Wait for its bounded local
            # connection failure before deleting the FK-linked fixture.
            def finished():
                response = e2e_client.get(
                    f"/api/agent-runs/{run_id}", headers=platform_admin.headers,
                )
                assert response.status_code == 200, response.text
                return response.json()["status"] in {
                    "completed", "failed", "cancelled", "timeout", "budget_exceeded", "contract_failed",
                }

            assert poll_until(finished, max_wait=10), "Owned enqueue run did not finish"
        if agent_id is not None:
            await db_session.execute(delete(Agent).where(Agent.id == agent_id))
        await db_session.execute(delete(AIModelProfile).where(AIModelProfile.id == profile.id))
        await db_session.execute(delete(AIProviderConnection).where(AIProviderConnection.id == connection.id))
        await db_session.commit()
