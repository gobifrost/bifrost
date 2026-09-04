"""Request-level regressions for /api/agent-runs pagination."""

from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent


pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def seeded_agent_runs(
    db_session: AsyncSession,
) -> AsyncGenerator[dict, None]:
    agent = Agent(
        id=uuid4(),
        name=f"Pagination Test Agent {uuid4().hex[:8]}",
        description="pagination-test",
        system_prompt="test",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=None,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(agent)
    await db_session.flush()

    base = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    runs: list[AgentRun] = []
    for index in range(3):
        run = AgentRun(
            id=uuid4(),
            agent_id=agent.id,
            trigger_type="api",
            status="completed",
            created_at=base + timedelta(minutes=index),
        )
        db_session.add(run)
        runs.append(run)

    await db_session.commit()

    try:
        yield {"agent": agent, "base": base, "runs": runs}
    finally:
        for run in runs:
            await db_session.execute(delete(AgentRun).where(AgentRun.id == run.id))
        await db_session.execute(delete(Agent).where(Agent.id == agent.id))
        await db_session.commit()


async def test_agent_runs_return_truthful_next_cursor(
    e2e_client,
    platform_admin,
    seeded_agent_runs,
):
    agent_id = str(seeded_agent_runs["agent"].id)

    first = e2e_client.get(
        "/api/agent-runs",
        params={"agent_id": agent_id, "limit": 1},
        headers=platform_admin.headers,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["items"]) == 1
    assert first_body["items"][0]["id"] == str(seeded_agent_runs["runs"][2].id)
    assert first_body["total"] == 3
    assert first_body["next_cursor"] == "1"

    second = e2e_client.get(
        "/api/agent-runs",
        params={"agent_id": agent_id, "limit": 1, "cursor": first_body["next_cursor"]},
        headers=platform_admin.headers,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["items"]) == 1
    assert second_body["items"][0]["id"] == str(seeded_agent_runs["runs"][1].id)
    assert second_body["next_cursor"] == "2"

    third = e2e_client.get(
        "/api/agent-runs",
        params={"agent_id": agent_id, "limit": 1, "cursor": second_body["next_cursor"]},
        headers=platform_admin.headers,
    )
    assert third.status_code == 200, third.text
    third_body = third.json()
    assert len(third_body["items"]) == 1
    assert third_body["items"][0]["id"] == str(seeded_agent_runs["runs"][0].id)
    assert third_body["next_cursor"] is None


async def test_agent_runs_reject_invalid_cursor(
    e2e_client,
    platform_admin,
    seeded_agent_runs,
):
    response = e2e_client.get(
        "/api/agent-runs",
        params={"agent_id": str(seeded_agent_runs["agent"].id), "cursor": "not-a-cursor"},
        headers=platform_admin.headers,
    )

    assert response.status_code == 422, response.text
