from __future__ import annotations

from uuid import uuid4

import pytest


pytestmark = pytest.mark.asyncio


async def test_enqueue_returns_immediately_queryable_run(
    e2e_client,
    platform_admin,
):
    name = f"Queued Agent {uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/agents",
        json={
            "name": name,
            "description": "Agent enqueue lifecycle test",
            "system_prompt": "Return a short acknowledgement.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "max_run_timeout": 5,
        },
        headers=platform_admin.headers,
    )
    assert created.status_code == 201, created.text

    accepted = e2e_client.post(
        "/api/agent-runs/enqueue",
        json={"agent_name": name, "input": {"ticket_id": 42}},
        headers=platform_admin.headers,
    )

    assert accepted.status_code == 202, accepted.text
    receipt = accepted.json()
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
