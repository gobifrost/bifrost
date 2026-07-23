"""GET /api/agent-runs/{id} delegation summaries."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agent_runs import AgentRun


pytestmark = pytest.mark.asyncio


def _create_agent(e2e_client, platform_admin, name: str) -> dict:
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": name,
            "description": "Delegation history test agent",
            "system_prompt": "Test delegated work.",
            "channels": [],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_run_detail_returns_ordered_child_summaries_with_agent_names(
    e2e_client,
    platform_admin,
    db_session: AsyncSession,
):
    root_agent = _create_agent(
        e2e_client, platform_admin, f"Coordinator {uuid4().hex[:8]}"
    )
    first_agent = _create_agent(
        e2e_client, platform_admin, f"Inventory Specialist {uuid4().hex[:8]}"
    )
    second_agent = _create_agent(
        e2e_client, platform_admin, f"Troubleshooting Specialist {uuid4().hex[:8]}"
    )

    root_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    started = datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
    root = AgentRun(
        id=root_id,
        agent_id=UUID(root_agent["id"]),
        trigger_type="api",
        status="completed",
        created_at=started,
    )
    # Insert in reverse chronological order so the response must honor
    # created_at rather than insertion order.
    second = AgentRun(
        id=second_id,
        agent_id=UUID(second_agent["id"]),
        parent_run_id=root_id,
        trigger_type="delegation",
        status="running",
        asked="Diagnose the device problem",
        did="Checked services and disk health",
        duration_ms=2300,
        created_at=started + timedelta(seconds=2),
    )
    first = AgentRun(
        id=first_id,
        agent_id=UUID(first_agent["id"]),
        parent_run_id=root_id,
        trigger_type="delegation",
        status="completed",
        asked="Find the affected device",
        did="Matched the ticket to WS-104",
        answered="WS-104 is the affected device",
        duration_ms=1200,
        created_at=started + timedelta(seconds=1),
    )
    db_session.add_all([root, second, first])
    await db_session.commit()

    try:
        response = e2e_client.get(
            f"/api/agent-runs/{root_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["child_run_ids"] == [str(first_id), str(second_id)]
        assert [child["id"] for child in body["child_runs"]] == [
            str(first_id),
            str(second_id),
        ]
        assert [child["agent_name"] for child in body["child_runs"]] == [
            first_agent["name"],
            second_agent["name"],
        ]
        assert body["child_runs"][0] == {
            "id": str(first_id),
            "agent_id": first_agent["id"],
            "agent_name": first_agent["name"],
            "status": "completed",
            "asked": "Find the affected device",
            "did": "Matched the ticket to WS-104",
            "answered": "WS-104 is the affected device",
            "duration_ms": 1200,
            "created_at": "2026-07-22T14:00:01Z",
        }
        assert body["child_runs"][1]["agent_name"] == second_agent["name"]
        assert body["child_runs"][1]["status"] == "running"
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == root_id))
        await db_session.commit()
        for agent in (root_agent, first_agent, second_agent):
            e2e_client.delete(
                f"/api/agents/{agent['id']}", headers=platform_admin.headers
            )


async def test_agent_scoped_history_includes_delegated_runs(
    e2e_client,
    platform_admin,
    db_session: AsyncSession,
):
    root_agent = _create_agent(
        e2e_client, platform_admin, f"Coordinator {uuid4().hex[:8]}"
    )
    child_agent = _create_agent(
        e2e_client, platform_admin, f"Delegated Specialist {uuid4().hex[:8]}"
    )
    root_id = uuid4()
    child_id = uuid4()
    created_at = datetime(2026, 7, 22, 15, 0, tzinfo=timezone.utc)
    db_session.add_all(
        [
            AgentRun(
                id=root_id,
                agent_id=UUID(root_agent["id"]),
                trigger_type="api",
                status="completed",
                created_at=created_at,
            ),
            AgentRun(
                id=child_id,
                agent_id=UUID(child_agent["id"]),
                parent_run_id=root_id,
                trigger_type="delegation",
                status="completed",
                asked="Investigate the endpoint",
                created_at=created_at + timedelta(seconds=1),
            ),
        ]
    )
    await db_session.commit()

    try:
        scoped_response = e2e_client.get(
            "/api/agent-runs",
            params={"agent_id": child_agent["id"]},
            headers=platform_admin.headers,
        )
        assert scoped_response.status_code == 200, scoped_response.text
        scoped_ids = {item["id"] for item in scoped_response.json()["items"]}
        assert str(child_id) in scoped_ids

        global_response = e2e_client.get(
            "/api/agent-runs",
            headers=platform_admin.headers,
        )
        assert global_response.status_code == 200, global_response.text
        global_ids = {item["id"] for item in global_response.json()["items"]}
        assert str(child_id) not in global_ids
    finally:
        await db_session.execute(delete(AgentRun).where(AgentRun.id == root_id))
        await db_session.commit()
        for agent in (root_agent, child_agent):
            e2e_client.delete(
                f"/api/agents/{agent['id']}", headers=platform_admin.headers
            )
